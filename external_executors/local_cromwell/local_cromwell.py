"""AWS lambda function to run a WDL on a local instance of Cromwell.

The general approach is to
    1. Parse whatever workflow-specific information is needed from the event.
    2. Create a script to
        a. Localize inputs to an EC2 instance
        b. Run Cromwell
        c. Record status updated in a DynamoDB table
    3. Create a UserData script that will pull the above script from the database
       and execute it.
    4. Run an EC2 instance with the UserData script.
    5. Return an id for the workflow. That subsequent calls can use to get status,
       logs, etc.
"""

import datetime
import functools
import json
import os
import shlex
import uuid

import boto3


# The standard Amazon Linux AMI. This will break outside of us-east-1, and
# probably for certain instance types too.
AMI = "ami-55ef662f"

# These are placed in the environment by the Cloudformation template. Rather
# than sticking os.environs all over the script, put all the Cloudformation
# variables here so I stop forgetting about them.
CLOUDFORMATION_VARIABLES = {
    "region": os.environ["REGION"],
    "instance_type": os.environ["INSTANCE_TYPE"],
    "volume_size": int(os.environ["VOLUME_SIZE"]),
    "table_name": os.environ["DB_TABLE"],
    "iam_profile": os.environ["IAM_PROFILE"],
    "subnet": os.environ["SUBNET"]

}

# This script is responsible for running downloading inputs, running Cromwell,
# and recording events. There are a couple levels of formatting and escaping:
# 1. It will get passed through python's "".format so things like job IDs can
#    be written directly into the script. This means that any literal { } need
#    to be doubled to {{ }}. We also have to escape any backslashes, so a
#    literal \ needs to be a \\.
# 2. The inputs, the WDL and input JSON get passed through shlex.quote so
#    special characters and quotes are handled correctly when inserted into the
#    script.
JOB_SCRIPT = """#!/bin/bash
set -x -e

# Function to write events to the DynamoDB table
function record_event {{
python3 << END
import boto3, datetime, uuid
event_id = str(uuid.uuid4())
ddb = boto3.resource("dynamodb", region_name="{region}")
table = ddb.Table("{db_table}")
table.put_item(
    Item={{
        "EventId": event_id,
        "JobId": "{job_id}",
        "EventType": "$1",
        "Message": "$2" or "empty",
        "Time": "{{:%Y-%m-%dT%H:%M:%SZ}}".format(datetime.datetime.utcnow())
    }}
)
END
}}

# Shutdown and record JobFailed on any non-zero return codes.
function fail {{
    set +e
    record_event "Failed"
    shutdown -c || true
    shutdown -H 5
}}

trap 'fail' EXIT

# Get dependencies for local cromwell
yum update -y
yum install -y docker java-1.8.0-openjdk-headless
service docker start
wget -q https://github.com/broadinstitute/cromwell/releases/download/30.2/cromwell-30.2.jar

# Record a job start event
record_event "Started"

# The inputs arrive as a JSON string, but write them to a file
echo {inputs_json} > inputs.json

# Similarly, the WDL comes as a string, so write it to a file Cromwell will read
echo {wdl} > workflow.wdl

# Finally, decode the base64-encoded dependencies into a file and unzip them.
if [ ! -z "{dependencies}" ]; then
    echo "{dependencies}" | base64 -d - > dependencies.zip
    unzip dependencies.zip
fi

# Iterate over the inputs and download any files. Then replace the entries
# that refer to remote locations with entries that refer to the local paths.
# TODO: Handle protocols other than http (gs, s3, ...)
python3 << END
import json, os, subprocess, tempfile
from google.cloud import storage
inputs_dict = json.load(open("inputs.json"))

def match_url(s):
    return s.startswith("http://") or \
           s.startswith("https://") or \
           s.startswith("gs://")

def localize_url(url):
    tmp_dir = tempfile.mkdtemp()
    if url.startswith("https://") or url.startswith("http://"):
        local_path = os.path.join(tmp_dir, os.path.basename(url))
        subprocess.run(["curl", "-o", local_path, url], check=True)
        return local_path
    elif url.startswith("gs://"):
        client = storage.client.Client.create_anonymous_client()
        path_parts = url.replace("gs://", "").split("/")
        bucket_name, *blob_parts = url.replace("gs://", "").split("/")
        bucket = client.bucket(bucket_name)
        blob_name = "/".join(blob_parts)
        blob = bucket.blob(blob_name)
        local_path = os.path.join(tmp_dir, blob_parts[-1])
        blob.download_to_filename(local_path)
        return local_path
    else:
        return url

def localize_inputs(obj, match_func, localize_func):
    if isinstance(obj, (list, tuple)):
        return [localize_inputs(e, match_func, localize_func) for e in obj]
    elif isinstance(obj, dict):
        return {{k: localize_inputs(v, match_func, localize_func) for k, v in obj.items()}}
    elif isinstance(obj, str):
        if match_func(obj):
            return localize_func(obj)
        else:
            return obj
    else:
        return obj

localized_inputs_dict = localize_inputs(inputs_dict, match_url, localize_url)
json.dump(localized_inputs_dict, open("localized_inputs.json", "w"))
END

# Record an inputs localized event
record_event "InputsLocalized"

# Run Cromwell and record the return code. Don't exit on failure here because
# we want the log.
record_event "CromwellStarting"
set +e
java8 -jar cromwell-30.2.jar run workflow.wdl --inputs localized_inputs.json 2>&1 | tee cromwell.log
cromwell_rc=${{PIPESTATUS[0]}}
set -e
record_event "CromwellFinished" "$cromwell_rc"

# Record log events. We can't pass the message as a bash variable, so don't
# use the record_event function.
python3 << END
import boto3, datetime, glob, json, os, uuid

# First we're going to record the workflow log, which is what cromwell writes
# to stdout and stderr. We tee'd it to cromwell.log above.
ddb = boto3.resource("dynamodb", region_name="{region}")
table = ddb.Table("{db_table}")

event_id = str(uuid.uuid4())
try:
    log = open("cromwell.log").read()
except IndexError:
    log = "No Cromwell logs found."

table.put_item(
    Item={{
        "EventId": event_id,
        "JobId": "{job_id}",
        "EventType": "WorkflowLog",
        "Message": log,
        "Time": "{{:%Y-%m-%dT%H:%M:%SZ}}".format(datetime.datetime.utcnow())
    }}
)

# Now record an event for the task logs, the stdout and stderr of each task.
# I guess this will just be a big json

task_logs_list = []
max_log_size = 5 << 10
event_id = str(uuid.uuid4())
for root, dirs, files in os.walk("/cromwell-executions/"):
    if "stdout" in files and "stderr" in files:
        try:
            stderr_path = os.path.join(root, "stderr")
            stderr = open(stderr_path).read(max_log_size)
        except Exception as exc:
            stderr = "Could not read stderr because {{}}".format(exc)

        try:
            stdout_path = os.path.join(root, "stdout")
            stdout = open(stdout_path).read(max_log_size)
        except Exception as exc:
            stdout = "Could not read stdout because {{}}".format(exc)

        try:
            rc_path = os.path.join(root, "rc")
            rc = int(open(rc_path).read().strip())
        except:
            rc = -1

        task_name = root[len("/cromwell-executions/"):]
        task_logs_list.append({{
            "name": task_name,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": rc
        }})

table.put_item(
    Item={{
        "EventId": event_id,
        "JobId": "{job_id}",
        "EventType": "TaskLogs",
        "Message": json.dumps(task_logs_list),
        "Time": "{{:%Y-%m-%dT%H:%M:%SZ}}".format(datetime.datetime.utcnow())
    }}

)
END

if [ $cromwell_rc == 0 ]; then
    record_event "Succeeded"
else
    record_event "Failed"
fi

# Turn off the trap or it records Failed twice.
trap - EXIT
shutdown -c || true
shutdown -H 5
"""

# This is the script passed as UserData. The size limit is 4KB, so we can't
# stick the WDL in here. It just retrieved the script from the database and
# runs it.
USER_DATA = """#!/bin/bash
yum update -y
yum install -y python36 python36-pip
pip-3.6 install boto3 google-cloud-storage

python3 << END
import boto3
from boto3.dynamodb.conditions import Key, Attr

ddb = boto3.resource("dynamodb", region_name="{region}")
table = ddb.Table("{db_table}")
response = table.query(
    KeyConditionExpression=Key("JobId").eq("{job_id}"),
    FilterExpression=Attr("EventType").eq("Script")
)
script = response["Items"][0]["Message"]

with open("local_cromwell_run.bash", "w") as f:
    f.write(script)
END

# Set a maximum run time of 90 minutes to kill stalled jobs.
shutdown -H 90 &

bash local_cromwell_run.bash
"""

# Define boto3 clients

def workflows_post(event, context):
    """Handle a POST to /workflows by creating a job.

    The event should be a dict with the following keys, based off of GA4GH's WES:
        - workflow_descriptor
            WDL string that describes the workflow that will be run.
        - workflow_params
            JSON string that has the inputs for WDL
        - workflow_type
            Ignored, we're only going to try to run WDLs
        - workflow_type_version
            Ignored
        - workflow_dependencies
            (Optional) base64 encoded ZIP file with WDL dependencies

    context is supplied by AWS and is described here:
        https://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html
    """

    def _record_event(table_name, job_id, event_type, message=None):
        """Record an event in the DynamoDB table."""

        ddb = boto3.resource("dynamodb", region_name=CLOUDFORMATION_VARIABLES["region"])
        table = ddb.Table(table_name)
        event_id = str(uuid.uuid4())

        table.put_item(
            Item={
                "EventId": event_id,
                "JobId": job_id,
                "EventType": event_type,
                "Message": message or "empty",
                "Time": "{:%Y-%m-%dT%H:%M:%SZ}".format(datetime.datetime.utcnow())
            }
        )

    ec2_client = boto3.client('ec2', region_name=CLOUDFORMATION_VARIABLES["region"])


    # Create a unique id for this job that. We'll pass it to the EC2 instance
    # that runs the job itself so it can use it to write status updates to the
    # DB.
    job_id = str(uuid.uuid4())

    record_event = functools.partial(
        _record_event, CLOUDFORMATION_VARIABLES["table_name"], job_id)

    try:
        workflow_dependencies = event["workflow_dependencies"]
    except KeyError:
        workflow_dependencies = ""

    record_event("Initializing")

    # Record the inputs in the table so we can query them later.
    record_event("WorkflowDescriptor", event["workflow_descriptor"])
    record_event("WorkflowParams", event["workflow_params"])
    if workflow_dependencies:
        record_event("WorkflowDependencies", workflow_dependencies)

    # Populate the job script with values for this request.
    job_script = JOB_SCRIPT.format(
        inputs_json=shlex.quote(event["workflow_params"]),
        wdl=shlex.quote(event["workflow_descriptor"]),
        dependencies=workflow_dependencies,
        job_id=job_id,
        db_table=CLOUDFORMATION_VARIABLES["table_name"],
        region=CLOUDFORMATION_VARIABLES["region"])

    # And write it to the database so the launched EC2 instance can get it and
    # we can return it in queries.
    record_event("Script", job_script)

    # Prepare the UserData script to pass to the new EC2 instance.
    user_data = USER_DATA.format(
        job_id=job_id,
        db_table=CLOUDFORMATION_VARIABLES["table_name"],
        region=CLOUDFORMATION_VARIABLES["region"])

    # Record it to help with debugging if necessary.
    record_event("UserData", user_data)

    # Launch the new EC2 instance that will run Cromwell.
    ec2_client.run_instances(
        ImageId=AMI,
        BlockDeviceMappings=[{
            "DeviceName": "/dev/xvda",
            "Ebs": {
                "VolumeSize": CLOUDFORMATION_VARIABLES["volume_size"],
                "VolumeType": "gp2"
                }
        }],
        InstanceType=CLOUDFORMATION_VARIABLES["instance_type"],
        MinCount=1,
        MaxCount=1,
        InstanceInitiatedShutdownBehavior='terminate',
        UserData=user_data,
        SubnetId=CLOUDFORMATION_VARIABLES["subnet"],
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "Name", "Value": "local-cromwell-portability-test"},
                {"Key": "JobId", "Value": job_id}
            ]}
        ],
        IamInstanceProfile={
            "Arn": CLOUDFORMATION_VARIABLES["iam_profile"]
        }
    )

    return {"workflow_id": job_id}


def wes_state_from_db_items(db_items):
    """Return a WES workflow state based off of a set of Items returned
    by a DynamoDB query for a single JobId.

    The permitted WES states are "Unknown", "Queued", "Running", "Paused",
    "Complete", "Error", "SystemError", "Canceled", and "Initializing".

    This function looks at all the events recorded for a job and decides
    what WES state the job is in.
    """
    # The progression of events should be this:
    # Initialzing
    # Started
    # InputsLocalized
    # CromwellStarting
    # CromwellFinished
    # Succeeded or Failed
    events = {k["EventType"] for k in db_items}

    if "Succeeded" in events:
        return "Complete"
    elif "Failed" in events:
        return "Error"
    elif "Started" in events:
        return "Running"
    elif "Initializing" in events:
        return "Initializing"

    return "Unknown"


def workflow_status(event, context):
    """Handle GET requests to /workflows/{workflow_id}/status

    Return the status of a given workflow.

    event is a dict with one key: "workflow_id"

    API Gateway is responsible for parsing the URL parameter, so it should
    show up to this function looking like a POST with a "workflow_id" in
    the body.

    context is supplied by AWS and is described here:
        https://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html
    """
    workflow_id = event["workflow_id"]

    ddb = boto3.resource("dynamodb", region_name=CLOUDFORMATION_VARIABLES["region"])
    table = ddb.Table(CLOUDFORMATION_VARIABLES["table_name"])
    response = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key('JobId').eq(workflow_id)
    )

    if not response["Count"]:
        error_dict = {
            "errorType": "NotFound",
            "httpStatus": "404",
            "requestId": context.aws_request_id,
            "message": "Workflow {} was not found".format(workflow_id)
            }

        raise Exception(json.dumps(error_dict))

    job_status = wes_state_from_db_items(response["Items"])

    return {
        "workflow_id": workflow_id,
        "state": job_status
    }

def workflow_log(event, context):
    """Handle GET requests to /workflow/{workflow_id}"""

    workflow_id = event["workflow_id"]
    ddb = boto3.resource("dynamodb", region_name=CLOUDFORMATION_VARIABLES["region"])
    table = ddb.Table(CLOUDFORMATION_VARIABLES["table_name"])
    db_response = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key('JobId').eq(workflow_id)
    )

    if not db_response["Count"]:
        error_dict = {
            "errorType": "NotFound",
            "httpStatus": "404",
            "requestId": context.aws_request_id,
            "message": "Workflow {} was not found".format(workflow_id)
            }

        raise Exception(json.dumps(error_dict))


    # This is a subset of the response defined by WES.
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

    response["state"] = wes_state_from_db_items(db_response["Items"])
    response["workflow_id"] = workflow_id

    for db_item in db_response["Items"]:
        if db_item["EventType"] == "WorkflowLog":
            response["workflow_log"]["stdout"] = db_item["Message"]
        elif db_item["EventType"] == "Started":
            response["workflow_log"]["start_time"] = db_item["Time"]
        elif db_item["EventType"] in ("Succeeded", "Failed"):
            response["workflow_log"]["end_time"] = db_item["Time"]
        elif db_item["EventType"] == "CromwellFinished":
            response["workflow_log"]["exit_code"] = db_item["Message"]
        elif db_item["EventType"] == "TaskLogs":
            task_logs_list = json.loads(db_item["Message"])
            response["task_logs"] = task_logs_list

    return response
