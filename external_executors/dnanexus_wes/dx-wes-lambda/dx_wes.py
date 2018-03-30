"""Lambdas to translate WDL workflow execution requests made via the GA4GH's
WES API to dnanexus.

Implements three WES endpoints:
    POST /workflows                         dnanexus_workflows_post
    GET /workflows/<workflow_id>/status     dnanexus_workflows_get_status
    GET /workflows/<workflow_id>            dnanexus_workflows_get

All state and computation occurs within dnanexus, so these lambdas don't need
to interact with any other AWS components.

When a POST request is made to /workflows, we need to handle three concerns:
    1. Copying any file inputs to dnanexus
    2. Running dxWDL to create the workflow object on dnanexus
    3. Running the new workflow object with the localized inputs

All three of those are accomplished by running "applets" or workflows on
dnanexus. To be able to query status and logs later, we tag all the executions
with a dnanexus "property" that we can use to find the relevant executions
later.
"""

import datetime
import json
import os
import subprocess
import uuid

# Needed to parallelize job log collection
from multiprocessing.dummy import Pool

# This is the dnanexus python API
import dxpy

# Two modules responsible for building and running file localization and dxWDL
# applets. Stored adjacent to this file in the lambda deployment package.
from localize_file import localize_file
from run_dxwdl import run_dxwdl


def launch_localization_jobs(inputs_dict, project, wes_id):
    """Walk through the inputs dict for a workflow execution and find URLs
     that need to be localized to dnanexus before the workflow can be run.

     Launch a localization job on dnanexus for each such URL and record the ids
     of those jobs.

    Args:
        inputs_dict (dict): The dictionary of inputs to the workflow. Note that
            this can have more or less arbitrary structure.
        project (string): Id of dnanexus project into which to localize the files
        wes_id (string): The WES workflow id that's been assigned to this workflow.
            Used to tag the uploaded files and put them in a folder.

    Returns:
        Tuple of (localized_inputs_dict, localization_job_ids)
            localized_inputs_dict is a dict where URLs have been replaced with dnanexus
                job ids
            localization_job_ids is a list of the ids of all localization jobs that have
                been launched on dnanexus
    """

    localization_job_ids = []

    def localize_url(url):
        """Launch the localization job and return a string that refers to the job's
        id.
        """
        response = json.loads(localize_file(url, project, wes_id))
        job_id = response["$dnanexus_link"]["job"]
        localization_job_ids.append(job_id)
        return 'localizer-' + job_id

    def match_url(s):
        """Return True if s looks like a URL we can do something with."""
        return s.startswith("http://") or \
            s.startswith("https://") or \
            s.startswith("gs://")

    def localize_inputs(obj, match_func, localize_func):
        """Recursively walk through the input dict, launching localization job
        as necessary.
        """

        if isinstance(obj, (list, tuple)):
            return [localize_inputs(e, match_func, localize_func) for e in obj]
        if isinstance(obj, dict):
            return {k: localize_inputs(v, match_func, localize_func) for k, v in obj.items()}
        if isinstance(obj, basestring):
            if match_func(obj):
                return localize_func(obj)
        return obj

    localized_inputs_dict = localize_inputs(inputs_dict, match_url, localize_url)

    return localized_inputs_dict, localization_job_ids

def set_dx_authorization(authorization_header):
    """Set up authorization to DNAnexus. This appears to be done through a
    module-level global, so we can just do it once.

    Args:
        authorization_header (string): The bearer token passed in the header of
            a request to DNAnexus. Looks like "Bearer: abc123xzy"

    Returns:
        auth_header_json (string): Stringified JSON that can be used to set
            environment variables for authentication.
    """

    dx_token = authorization_header.replace("Bearer ", "")
    auth_header = {
        "auth_token_type": "Bearer",
        "auth_token": dx_token
    }
    dxpy.set_security_context(auth_header)
    return json.dumps(auth_header)

def dnanexus_workflows_post(event, context):
    """Handle a WES workflows POST request and turn it into a DNAnexus
    /{workflow-id}/run request.
    """

    # Create an id for the workflow. We'll return this to the user and also
    # tag the dnanexus executions with it.
    wes_workflow_id = str(uuid.uuid4())

    # Set up the token for making DNAnexus API requests
    set_dx_authorization(event["headers"]["Authorization"])

    # Get a reference to the "project" on DNAnexus in which we'll be building
    # and executing this workflow. We'll put everything in a folder named after
    # the WES workflow id so it doesn't get too cluttered, and we avoid name
    # collisions.
    project_id = event["body"]["key_values"]["dx_project_id"]
    dxpy.set_project_context(project_id)
    dxpy.DXProject(project_id).new_folder('/' + wes_workflow_id)

    # Find URLs that need to be localized, and launch jobs to do that. Note
    # that after this step, we'll have jobs pending on dnanexus.
    inputs_dict = json.loads(event["body"]["workflow_params"])
    dx_localized_input_dict, localization_job_ids = launch_localization_jobs(
        inputs_dict, project_id, wes_workflow_id)

    # Launch the dnanexus job that will run dxWDL.
    run_dxwdl(
        event["body"]["workflow_descriptor"],
        json.dumps(dx_localized_input_dict),
        wes_workflow_id,
        project_id,
        (event["body"]["workflow_dependencies"]
         if "workflow_dependencies" in event["body"] else None),
        localization_job_ids
    )

    return {"workflow_id": wes_workflow_id}

def dx_to_wes_state(dx_state):
    """Convert the state returned by DNAnexus to something from the list of states
    defined by WES.
    """

    if dx_state in ("running", "waiting_on_input", "waiting_on_output"):
        return "RUNNING"
    elif dx_state == "runnable":
        return "QUEUED"
    elif dx_state in ("failed", "terminating", "terminated"):
        return "EXECUTOR_ERROR"
    elif dx_state in "done":
        return "COMPLETE"

    return "UNKNOWN"

def dnanexus_workflows_get_status(event, context):
    """Handle GET /workflows/{workflow_id}/status.

    Args:
        event (dict): has a key "workflow_id" that's been taking from the URL.
            This is the id that was generated when the POST request was made,
            and the dnanexus executions we care about should be tagged with it.
        context (dict): an AWS context object that we ignore
    """

    wes_workflow_id = event["workflow_id"]
    set_dx_authorization(event["headers"]["Authorization"])

    # Ths base_job is the job that ran dxWDL and launched the workflow itself
    # as a subjob. We only need to query its status to find the status of the
    # whole execution since the success or failure of its child workflow will
    # be propagated to it.
    try:
        base_job = list(dxpy.find_jobs(
            properties={"wes_id": wes_workflow_id},
            name="WES dxWDL Runner",
            return_handler=True))[0]
    except IndexError:
        error_dict = {
            "errorType": "NotFound",
            "httpStatus": "404",
            "requestId": context.aws_request_id,
            "message": "Workflow {} was not found".format(wes_workflow_id)
            }
        return error_dict

    # Query the dnanexus state and translate that to a WES state
    dx_state = base_job.describe()["state"]
    wes_state = dx_to_wes_state(dx_state)

    return {
        "workflow_id": wes_workflow_id,
        "state": wes_state
    }

def dnanexus_workflows_get(event, context):
    """Handle GET /workflows/{workflow_id}.

    Args:
        event (dict): has a key "workflow_id" that's been taking from the URL.
            This is the id that was generated when the POST request was made,
            and the dnanexus executions we care about should be tagged with it.
        context (dict): an AWS context object that we ignore
    """

    auth_header = set_dx_authorization(event["headers"]["Authorization"])
    wes_workflow_id = event["workflow_id"]

    # First try to find the dxWDL job that's the parent job of everything
    try:
        base_job = list(dxpy.find_jobs(
            properties={"wes_id": wes_workflow_id},
            name="WES dxWDL Runner",
            return_handler=True))[0]
    except IndexError:
        error_dict = {
            "errorType": "NotFound",
            "httpStatus": "404",
            "requestId": context.aws_request_id,
            "message": "Workflow {} was not found".format(wes_workflow_id)
            }
        return error_dict

    child_jobs = list(dxpy.find_jobs(root_execution=base_job.get_id(), return_handler=True))
    child_job_ids = [j.get_id() for j in child_jobs]

    response = {
        "state": "",
        "workflow_id": "",
        "workflow_log": {
            "start_time": "",
            "end_time": "",
            "stdout": "",
            "stderr": "",
            "exit_code": -1
        },
        "task_logs": []
    }

    dx_state = base_job.describe()["state"]
    wes_state = dx_to_wes_state(dx_state)
    response["state"] = wes_state

    def get_logs_for_job(dx_job_id):
        """Retrieve the logs for single DXJob."""

        dx_exe_path = os.path.abspath("bin/dx")
        cmd = ["dx", "watch", "-q", "--no-timestamps", "--get-streams", "--no-follow", dx_job_id]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={"DX_SECURITY_CONTEXT": auth_header,
                 "PYTHONPATH": ':'.join([os.environ.get("PYTHONPATH", ""),
                                         os.path.dirname(os.path.dirname(dx_exe_path))]),
                 "PATH": ':'.join([os.environ["PATH"], os.path.dirname(dx_exe_path)])}
        )
        stdout, stderr = proc.communicate()
        return stdout

    pool = Pool(8)

    jobs_to_logs = dict(zip(
        child_job_ids,
        pool.map(get_logs_for_job, child_job_ids)))

    for job_id in child_job_ids:
        dx_job = dxpy.DXJob(job_id)
        job_desc = dx_job.describe()

        task_name = job_desc["executableName"]
        time_fmt = "{:%Y-%m-%dT%H:%M:%S}"
        try:
            start_time = time_fmt.format(datetime.datetime.fromtimestamp(
                job_desc["startedRunning"] / 1000))
        except:
            start_time = ""
        try:
            end_time = time_fmt.format(datetime.datetime.fromtimestamp(
                job_desc["stoppedRunning"] / 1000))
        except:
            end_time = ""

        try:
            log = jobs_to_logs[job_id]
        except:
            log = ""

        response["task_logs"].append({
            "name": task_name + ":" + job_id,
            "start_time": start_time,
            "end_time": end_time,
            "stdout": log,
            "stderr": "",
            "exit_code": -1
        })

    return response
