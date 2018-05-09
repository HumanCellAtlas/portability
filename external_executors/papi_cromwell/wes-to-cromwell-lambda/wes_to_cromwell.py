import base64
import binascii
import io
import json
import zipfile

import requests

AUTH = ("", "")
URL = ""
LABELS = {"project": "wes-cromwell-ga4gh"}

# Cromwell and WES refer to workflow statuses differently
CROMWELL_TO_WES_STATUS = {
    "Submitted": "QUEUED",
    "Running": "RUNNING",
    "Aborting": "CANCELED",
    "Failed": "EXECUTOR_ERROR",
    "Succeeded": "COMPLETE",
    "Aborted": "CANCELED"
}

def wes_service_info_get(event, context):
    """Return information about this service."""

    service_info = {
        "supported_wes_versions": ["0.2.1"],
        "supported_filesystem_protocols": ["gs"],
        "workflow_engine_versions": {
            "cromwell": "31"
        },
        "default_workflow_engine_parameters": [],
        "system_state_counts": {
            "zero": 0
        },
        "auth_instructions_url": "",
        "workflow_type_versions": {
            "huh": {
                "worfklow_type_version": [
                    "wdl",
                    "gzip",
                    "main.wdl"
                ]
            }
        },
        "tags": {}
    }

    return service_info

def wes_workflows_get(event, context):
    """Return some workflows.

    WES wants this to support pagination, but Cromwell neither supports
    pagination in its query endpoint, and I don't think it guarantees any
    particular order. So we're a little stuck.

    WES also has a field label with this instruction:

        For each key, if the key’s value is empty string then match workflows
        that are tagged with this key regardless of value.

    I'm not quite sure how to parse that. The field is a string?
    """

    wes_response = {
        "workflows": [],
        "next_page_token": ""
    }

    cromwell_response = requests.get(
        URL + "/query",
        auth=AUTH
    )

    for result in cromwell_response.json()["results"]:
        wes_response["workflows"].append(
            {
                "workflow_id": result["id"],
                "status": CROMWELL_TO_WES_STATUS[result["status"]]
            }
        )

    return wes_response

def wes_workflows_post(event, context):
    """Submit a workflow execution request."""

    # First, we need to figure out if we'rt working with a zip file or a string
    entry_point_wdl_string = None
    is_zip_file = True
    try:
        z = zipfile.ZipFile(io.BytesIO(base64.b64decode(event["workflow_descriptor"])), 'r')

        # We need to fine the entry point WDL and pull that out. That's been
        # placed in the workflow_url parameter
        for zipinfo in z.filelist:
            if zipinfo.filename == event["workflow_url"]:
                entry_point_wdl_string = z.read(zipinfo)

        # If we got far enough to decode and extract the file but still didn't
        # fine the entry point, then that's bad.
        if not entry_point_wdl_string:
            raise RuntimeError("No entry point WDL found! {}: {}".format(
                event["workflow_url"], [k.filename for k in z.filelist]))

    # We're okay with errors thrown by base64 or zipfile
    except (binascii.Error, zipfile.BadZipFile):
        is_zip_file = False
        print("Assuming the workflow descriptor is just a WDL string.")

    if not entry_point_wdl_string:
        entry_point_wdl_string = event["workflow_descriptor"]

    cromwell_form = {
        "workflowSource": io.StringIO(entry_point_wdl_string),
        "workflowInputs": io.StringIO(json.dumps(event["workflow_params"])),
        "workflowType": io.StringIO("WDL"),
        "labels": io.StringIO(json.dumps(LABELS))
    }

    if is_zip_file:
        cromwell_form["workflowDependencies"] = io.BytesIO(
            base64.b64decode(event["workflow_descriptor"]))

    cromwell_response = requests.post(
        URL,
        auth=AUTH,
        files=cromwell_form
    )

    wes_response = {
        "workflow_id": cromwell_response.json()["id"]
    }

    return wes_response

def wes_workflows_workflow_id_get(event, context):

    wes_log_skeleton = {
        "name": "",
        "cmd": [
            ""
        ],
        "start_time": "",
        "end_time": "",
        "stdout": "",
        "stderr": "",
        "exit_code": 0
    }

    wes_response = {
        "workflow_id": "",
        "request": {},
        "state": "",
        "workflow_log": wes_log_skeleton,
        "task_logs": [],
        "outputs": {}
    }

    cromwell_response = requests.get(
        URL + "/" + event["workflow_id"] + "/metadata",
        auth=AUTH
    )
    cromwell_response_json = cromwell_response.json()

    wes_response["workflow_id"] = cromwell_response_json["id"]
    wes_response["request"] = {
        "workflow_descriptor": cromwell_response_json.get("submittedFiles", {}).get("workflow"),
        "workflow_params": json.loads(cromwell_response.get("submittedFiles", {}).get("inputs")),
        "workflow_type": cromwell_response_json.get("submittedFiles", {}).get("workflowType")
    }
    wes_response["state"] = CROMWELL_TO_WES_STATUS[cromwell_response_json["status"]]
    wes_response["start_time"] = cromwell_response_json.get("start")
    wes_response["end_time"] = cromwell_response_json.get("end")

    wes_response["outputs"] = cromwell_response_json.get("outputs", {})

    return wes_response

def wes_workflows_workflow_id_delete(event, context):
    return {}

def wes_workflows_workflow_id_status_get(event, context):
    cromwell_response = requests.get(
        URL + "/" + event["workflow_id"] + "/status",
        auth=AUTH
    )

    return {
        "workflow_id": cromwell_response.json()["id"],
        "state": CROMWELL_TO_WES_STATUS[cromwell_response.json()["status"]]
    }
