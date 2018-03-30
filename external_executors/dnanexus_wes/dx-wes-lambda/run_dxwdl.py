"""Find or build a dxWDL applet on dnanexus."""

import sys

import dxpy

# Two pieces of metadata we can use to tell if this applet is already built and
# available on dnanexus.
APPLET_VERSION = "0.0.17"
APPLET_NAME = "wes_dxwdl_runner"

# The code for the applet itself. This downloads dxWDL, replaces remote URLs
# with dnanexus file ids, runs dxWDL, and runs the workflow that dxWDL creates.
APPLET_CODE = """
import dxpy
import os
import re
import subprocess

DX_JOB_PATTERN = "localizer-job-\w{24}"

@dxpy.entry_point("main")
def main(workflow_descriptor, workflow_params, wes_id, project, workflow_dependencies=None):

    with open("workflow.wdl", "w") as wdl_file:
        wdl_file.write(workflow_descriptor)

    replaced_workflow_params = str(workflow_params)
    for job_id in re.findall(DX_JOB_PATTERN, workflow_params):
        dx_job = dxpy.DXJob(job_id.replace("localizer-", ""))
        file_id = dx_job.describe()["output"]["localized_file"]["$dnanexus_link"]
        replaced_workflow_params = replaced_workflow_params.replace(job_id, "dx://" + file_id)

    with open("dx_inputs.json", "w") as inputs_json_file:
        inputs_json_file.write(replaced_workflow_params)
    print replaced_workflow_params

    if workflow_dependencies:
        with open("dependencies.b64", "w") as deps_file:
            deps_file.write(workflow_dependencies)
        b64_cmd = "cat dependencies.b64 | base64 -d > dependencies.zip"
        proc = subprocess.Popen(b64_cmd, shell=True)
        proc.communicate()
        unzip_cmd = ["unzip", "-d", "wdl_dependencies/", "dependencies.zip"]
        proc = subprocess.Popen(unzip_cmd)
        proc.communicate()

    download_dxwdl_cmd = ["wget", "-q", "https://github.com/dnanexus/dxWDL/releases/download/0.61.1/dxWDL-0.61.1.jar"]
    proc = subprocess.Popen(download_dxwdl_cmd)
    proc.communicate()

    dxpy.set_project_context(project)
    dxwdl_cmd = ["java", "-jar", "dxWDL-0.61.1.jar", "compile", "workflow.wdl",
                 "-inputs", "dx_inputs.json", "-destination", "/" + wes_id]
    if workflow_dependencies:
        dxwdl_cmd.extend(["-imports", "wdl_dependencies"])

    env = os.environ.copy()
    env["DX_WORKSPACE_ID"] = project
    env["DX_PROJECT_CONTEXT_ID"] = project
    proc = subprocess.Popen(
        dxwdl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env
    )
    stdout, stderr = proc.communicate()
    print stdout
    print stderr

    dx_workflow_id = stdout.strip()

    dx_workflow = dxpy.DXWorkflow(dx_workflow_id)
    dx_inputs = json.load(open("dx_inputs.dx.json"))

    dx_analysis = dx_workflow.run(dx_inputs)
"""

def run_dxwdl(workflow_descriptor, workflow_params, wes_id,
              project, workflow_dependencies=None, localization_jobs=None):
    """Run the dxWDL applet.

    Args:
        workflow_descriptor (string): from WES, the main WDL to execution
        workflow_params (string): also from WES, stringified JSON of the inputs
            to the workflow
        wes_id (string): WES workflow id that was generated for this workflow
        project (string): dnanexus project id in which dxWDL should run
        workflow_dependencies (string): if required, a base64 encoded zip
            archive of WDL imports
        localization_jobs (list of strings): URL localizer jobs running on
            inputs to this workflow
    """

    # Build or find the dxWDL applet
    dxwdl_applet = get_dxwdl_applet()

    inputs_dict = {
        "workflow_descriptor": workflow_descriptor,
        "workflow_params": workflow_params,
        "wes_id": wes_id,
        "project": project
    }
    if workflow_dependencies:
        inputs_dict["workflow_dependencies"] = workflow_dependencies

    # Run the dxWDL applet. Note that it can't start until the localizer jobs
    # are complete or there won't be input files available yet.
    dxwdl_job = dxwdl_applet.run(
        inputs_dict,
        project=project,
        depends_on=localization_jobs if localization_jobs else [],
        properties={"wes_id": wes_id}
    )


def get_dxwdl_applet():
    """Build or find the applet to run dxWDL."""

    found_applets = list(dxpy.find_data_objects(
        name=APPLET_NAME,
        properties={"version": APPLET_VERSION},
        classname="applet",
        state="closed",
        return_handler=True
    ))

    if found_applets:
        return found_applets[0]
    else:
        return build_applet()


def build_applet():
    """Build the dxWDL applet."""

    dx_applet_id = dxpy.api.applet_new({
        "name": APPLET_NAME,
        "title": "WES dxWDL Runner",
        "dxapi": dxpy.API_VERSION,
        "project": dxpy.PROJECT_CONTEXT_ID,
        "properties": {"version": APPLET_VERSION},
        "inputSpec": [
            {
                "name": "workflow_descriptor",
                "class": "string"
            },
            {
                "name": "workflow_params",
                "class": "string"
            },
            {
                "name": "workflow_dependencies",
                "class": "string",
                "optional": True
            },
            {
                "name": "project",
                "class": "string"
            },
            {
                "name": "wes_id",
                "class": "string"
            }
        ],
        "outputSpec": [],
        "runSpec": {
            "code": APPLET_CODE,
            "interpreter": "python2.7",
            "systemRequirements": {
                "*": {"instanceType": "mem1_ssd1_x4"}},
            "execDepends": [
                {
                    "name": "openjdk-8-jre-headless",
                },
                {
                    "name": "dx-toolkit"
                }
            ]
        },
        "access": {
            "network": ["*"],
            "project": "CONTRIBUTE"
        },
        "release": "14.04"
    })

    return dxpy.DXApplet(dx_applet_id["id"])

if __name__ == "__main__":
    run_dxwdl(*sys.argv[1:5])
