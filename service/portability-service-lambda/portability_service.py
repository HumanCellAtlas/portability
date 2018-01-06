"""Service to run workflows in different environments, demonstrating their
portability.
"""
import base64
import datetime
import enum
import io
import json
import os
import uuid
import zipfile

import boto3
from boto3.dynamodb.conditions import Key
import requests

#
# NB: Mentions of "WES" in this file are aspirational. Nothing here quite
# matches the currently published GA4GH WES spec, but that's a goal.
#

# Set of environment variables passed here from the Cloudformation template,
# which is in this same repo, portability_service_template.json
CLOUDFORMATION_VARIABLES = {
    "environments_table": os.environ["ENVIRONMENTS_TABLE"],
    "tests_table": os.environ["TESTS_TABLE"],
    "region": os.environ["REGION"]
}

class EnvironmentEvent(enum.Enum):
    """Enumeration of the different events the occur for a test in an environment."""

    # The submission to the environment failed.
    SUBMISSION_FAILED = "SubmissionFailed"

    # The submission to the environment succeeded, but we have no information
    # about the test job itself.
    SUBMISSION_SUCCEEDED = "SubmissionSucceeded"

    # The test job has completed in the environment, and it failed.
    JOB_FAILED = "JobFailed"

    # The test job has completed in the environment, and it succeeded.
    JOB_SUCCEEDED = "JobSucceeded"

    # The test job is still running in the environment
    JOB_RUNNING = "JobRunning"


class PortabilityTestState(enum.Enum):
    """Enumeration of states for the entire portability test."""

    # Jobs succeeded in all environments.
    SUCCEEDED = "Succeeded"

    # At least one job or submission failed.
    FAILED = "Failed"

    # At least one job is still running.
    RUNNING = "Running"


def environments_table():
    """Return the boto3 DynamoDB Table object for the environments table."""

    ddb = boto3.resource("dynamodb", region_name=CLOUDFORMATION_VARIABLES["region"])
    table = ddb.Table(CLOUDFORMATION_VARIABLES["environments_table"])
    return table


def tests_table():
    """Return the boto3 DynamoDB Table object for the tests table."""
    ddb = boto3.resource("dynamodb", region_name=CLOUDFORMATION_VARIABLES["region"])
    table = ddb.Table(CLOUDFORMATION_VARIABLES["tests_table"])
    return table


def record_test_event(test_id, environment_id, environment_event, message=None):
    """Record and event in the tests table.

    Sticks things in six fields:
        - EventId
            A unique uuid for this event
        - TestId
            A uuid for the "test", that is the submission of a WDL to the service and
            all associated responses from remote environments
        - EnvironmentId
            (Optional) the uuid for the remote environment associated with this event
        - EventType
            A member of EnvironmentEvent
        - Message
            Some message associated with the event
        - Time
            A UTC timestamp
    """
    event_id = str(uuid.uuid4())

    tests_table().put_item(
        Item={
            "EventId": event_id,
            "TestId": test_id,
            "EnvironmentId": environment_id,
            "EventType": environment_event.value,
            "Message": message or "empty",
            "Time": "{:%Y-%m-%dT%H:%M:%SZ}".format(datetime.datetime.utcnow())
        }
    )


def wes_submit(environment_id, wdl, params, dependency_wdls):
    """Submit a workflow to an endpoint using the WES schema."""

    # First get the WES endpoint to submit to
    ddb_response = environments_table().query(
        KeyConditionExpression=Key("EnvironmentId").eq(environment_id)
    )
    base_url = ddb_response["Items"][0]["Url"]
    workflows_url = os.path.join(base_url, "workflows")
    headers = ddb_response["Items"][0]["Headers"]

    # Then convert any dependency WDLs into a base64-encoded ZIP file.
    # Taken mostly from here:
    # https://github.com/broadinstitute/cromwell-tools/blob/2a3a803929fc6077b35029860f0e510c018f498d/cromwell_tools/cromwell_tools.py#L160-L177
    if dependency_wdls:
        buf = io.BytesIO()
        zip_buffer = zipfile.ZipFile(buf, 'w')
        for dependency_wdl in dependency_wdls:
            zip_buffer.writestr(
                dependency_wdl["name"],
                dependency_wdl["code"]
            )

        zip_buffer.close()

        b64_encoded_dependencies = base64.b64encode(buf.getvalue())
    else:
        b64_encoded_dependencies = None

    post_data = {
        "workflow_descriptor": wdl,
        "workflow_params": params,
    }
    if b64_encoded_dependencies:
        post_data["workflow_dependencies"] = b64_encoded_dependencies.decode()

    wes_response = requests.post(workflows_url, headers=headers, json=post_data)

    # This raises an exception for any error responses
    wes_response.raise_for_status()

    # Parse the json response to get the workflow Id
    return json.loads(wes_response.text)["workflow_id"]


def wes_status(environment_id, workflow_id):
    """Request a workflow status using WES."""
    ddb_response = environments_table().query(
        KeyConditionExpression=Key("EnvironmentId").eq(environment_id)
    )
    base_url = ddb_response["Items"][0]["Url"]
    status_url = os.path.join(base_url, "workflows", workflow_id, "status")
    headers = ddb_response["Items"][0]["Headers"]

    wes_response = requests.get(status_url, headers=headers)
    wes_response.raise_for_status()

    return json.loads(wes_response.text)["state"]


def environments_post(event, context):
    """Handle POST to /environments by registering a new external execution environment.

    event is a dict that should have keys
        - name
            Name of the environment to use when reporting status, etc.
        - url
            Base URL for submitting workflows to the environment
        - schema
            Workflow submission schema. Ignored for now, but should be WES once that's
            finalized.
        - headers
            dict of headers to include when making requests to the url
    """

    environment_id = str(uuid.uuid4())

    environments_table().put_item(
        Item={
            "EnvironmentId": environment_id,
            "Url": event["url"],
            "Name": event["name"],
            "Schema": event["schema"],
            "Headers": event.get("headers", "none")
        }
    )

    return {"environment_id": environment_id}


def environments_get(event, context):
    """Handle GET to /environments by returning a list of registered environments."""

    response = environments_table().scan()

    output = []
    for item in response["Items"]:
        output.append({"environment_id": item["EnvironmentId"],
                       "name": item["Name"],
                       "url": item["Url"],
                       "schema": item["Schema"]})

    return {"environments": output}


def portability_tests_post(event, context):
    """Handle POST to /portability_tests by submitting workflow requests to registered external
    environments.

    event is a dict that should have keys
        - workflow_descriptor (string)
            The main WDL file to be executed
        - workflow_params (string)
            JSON string of inputs to the main WDL
        - workflow_dependencies (list of strings)
            List of WDL strings for dependency WDLs
    """

    # Read all the registered environments so we can iterate over them
    all_environments = environments_table().scan()["Items"]

    wdl = event["workflow_descriptor"]
    params = event["workflow_params"]

    test_id = str(uuid.uuid4())

    for environment in all_environments:
        env_id = environment["EnvironmentId"]

        try:
            workflow_id = wes_submit(
                env_id, wdl, params, event.get("workflow_dependencies"))
            record_test_event(test_id, env_id, EnvironmentEvent.SUBMISSION_SUCCEEDED,
                              workflow_id)
        except Exception as e:
            record_test_event(test_id, env_id, EnvironmentEvent.SUBMISSION_FAILED, str(e))

    return {"test_id": test_id}


def portability_tests_get_status(event, context):
    """Handle GET to /portability_tests/{test_id}/status."""

    # Get all the db entries for a submission to an environment for this test
    response = tests_table().query(
        KeyConditionExpression=Key("TestId").eq(event["test_id"])
    )
    items = response["Items"]

    # 404 if we don't find anything
    if not items:
        error_dict = {
            "errorType": "NotFound",
            "httpStatus": "404",
            "requestId": context.aws_request_id,
            "message": "Test {} was not found".format(event["test_id"])
        }

        raise Exception(json.dumps(error_dict))

    # This is a set of all the environments to which the test workflow
    # descriptor was submitted.
    submitted_environment_ids = set([
        i["EnvironmentId"] for i in items
        if EnvironmentEvent(i["EventType"]) in (
            EnvironmentEvent.SUBMISSION_SUCCEEDED, EnvironmentEvent.SUBMISSION_FAILED
            )])

    environment_statuses = []

    for environment_id in submitted_environment_ids:
        all_env_events = [i for i in items if i["EnvironmentId"] == environment_id]
        event_types = [EnvironmentEvent(i["EventType"]) for i in all_env_events]

        # First check if the submission failed
        if EnvironmentEvent.SUBMISSION_FAILED in event_types:
            env_status = {"environment_id": environment_id, "workflow_id": None,
                          "state": EnvironmentEvent.SUBMISSION_FAILED}
            environment_statuses.append(env_status)
            continue

        # Check if there's a submission succeeded event. If not something very
        # strange is happening
        if EnvironmentEvent.SUBMISSION_SUCCEEDED not in event_types:
            error_dict = {
                "errorType": "NotFound",
                "httpStatus": "500",
                "requestId": context.aws_request_id,
                "message": "No submission found for environment {} test {}".format(
                    event["test_id"],
                    environment_id)
            }
            raise Exception(json.dumps(error_dict))

        # Okay great, the submission was successful, and we recorded an event
        # for that. So not we can retrieve the workflow_id the remote
        # environment gave us when we submitted it
        workflow_id = [
            k for k in all_env_events
            if k["EventType"] == EnvironmentEvent.SUBMISSION_SUCCEEDED.value][0]["Message"]

        # Now see if we already have a terminal event recorded for this
        # test/environment. This means that in the past we asked the remote
        # environment if the job was done, and it told us that is succeeded or
        # failed. If this is true, we don't need to make another request to the
        # remote environment.
        if EnvironmentEvent.JOB_FAILED in event_types:
            env_status = {"environment_id": environment_id, "workflow_id": workflow_id,
                          "state": EnvironmentEvent.JOB_FAILED}
            environment_statuses.append(env_status)
            continue

        if EnvironmentEvent.JOB_SUCCEEDED in event_types:
            env_status = {"environment_id": environment_id, "workflow_id": workflow_id,
                          "state": EnvironmentEvent.JOB_SUCCEEDED}
            environment_statuses.append(env_status)
            continue

        # Well we've made it here. That means the submission to the remote
        # environment succeeded, but we don't yet know how it turned out. So
        # we're going to have to ask. We'll use WES to do that.
        status = wes_status(environment_id, workflow_id)

        # Now we have a WES status. We'll translate it a bit to a portability
        # test status. We'll put a hopeful spin on the non-terminal statuses:
        wes_to_port = {
            "Unknown": EnvironmentEvent.JOB_RUNNING,
            "Queued": EnvironmentEvent.JOB_RUNNING,
            "Paused": EnvironmentEvent.JOB_RUNNING,
            "Running": EnvironmentEvent.JOB_RUNNING,
            "Complete": EnvironmentEvent.JOB_SUCCEEDED,
            "Error": EnvironmentEvent.JOB_FAILED,
            "SystemError": EnvironmentEvent.JOB_FAILED,
            "Canceled": EnvironmentEvent.JOB_FAILED,
            "Initializing": EnvironmentEvent.JOB_RUNNING}

        test_status = wes_to_port[status]

        env_status = {"environment_id": environment_id, "workflow_id": workflow_id,
                      "state": test_status}
        environment_statuses.append(env_status)

        # And finally, if we just observed a terminal state, record it so we
        # don't have to check again.
        if test_status in (EnvironmentEvent.JOB_FAILED,
                           EnvironmentEvent.JOB_SUCCEEDED):
            record_test_event(event["test_id"], environment_id, test_status)

    # It's not over, we need to set a status for the whole test.
    all_statuses = set([v["state"] for v in environment_statuses])

    if (EnvironmentEvent.SUBMISSION_FAILED in all_statuses or
            EnvironmentEvent.JOB_FAILED in all_statuses):
        overall_status = PortabilityTestState.FAILED
    elif all(k == EnvironmentEvent.JOB_SUCCEEDED for k in all_statuses):
        overall_status = PortabilityTestState.SUCCEEDED
    else:
        overall_status = PortabilityTestState.RUNNING

    # We need to get the string values for the enums before we return
    stringified_statuses = [
        {k: es[k].value if k == "state" else es[k] for k in es}
        for es in environment_statuses
    ]
    return {"state": overall_status.value,
            "environment_statuses": stringified_statuses
           }
