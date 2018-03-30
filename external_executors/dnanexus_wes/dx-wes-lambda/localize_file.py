"""Applet to localize files to DNAnexus.

Create or find an applet in dnanexus that can copy in a remote URL.
"""

import json
import sys

import dxpy

# Two pieces of metadata we can use to tell if this applet is already built and
# available on dnanexus.
APPLET_VERSION = "0.1.4"
APPLET_NAME = "wes_url_localizer"

# The code for the applet itself. This just reads the URL into a dnanexus file
# object.
APPLET_CODE = """
import dxpy
import requests
import google.cloud.storage

@dxpy.entry_point("main")
def main(url, project, folder):

    if url.startswith("gs://"):
        client = google.cloud.storage.client.Client.create_anonymous_client()
        path_parts = url.replace("gs://", "").split("/")
        bucket_name = path_parts[0]
        bucket = client.bucket(bucket_name)
        blob_name = "/".join(path_parts[1:])
        blob = bucket.blob(blob_name)
        public_url = blob.public_url
    elif url.startswith("https://") or url.startswith("http://"):
        public_url = url

    dx_file = dxpy.new_dxfile(
        name=os.path.basename(url),
        mode="w",
        folder=folder,
        parents=True,
        project=project)

    try:
        response = requests.get(public_url, stream=True)
        for chunk in response.iter_content(chunk_size=1<<24):
            dx_file.write(chunk)

    # Close and return the ID prefixed with the new scheme, dx
    finally:
        dx_file.close()

    return {"localized_file": dxpy.dxlink(dx_file.get_id())}
"""

def localize_file(url, project, wes_id):
    """Run the applet to localize a file at a URL to a project and folder
    within dnanexus.

    Args:
        url (string): remote url to localize
        project (string): dnanexus project id where the file should go. This
            should look like project-\w{24}
        wes_id (string): the WES workflow id that was given to the user when
            the workflow request was made. This is used to tag the jobs and
            create a folder where the localized files should go.

    Returns:
        Stringified JSON of a future that refers to the output of the dnanexus
        job that's copying the URL to dnanexus. Looks like this:
        '{"$dnanexus_link": {"job": "job-xxx", "field": "localized_file"}}'
    """

    # Find or build the localizer applet (both are very fast)
    localizer_applet = get_localizer_applet()

    # Run the localization job. This is asynchronous and returns a job id
    # immediately
    localizer_job = localizer_applet.run(
        {
            "url": url,
            "project": project,
            "folder": '/' + wes_id
        },
        project=project,
        properties={"wes_id": wes_id}
    )

    # Retunr JSON that refers to the future output of the localization job
    return json.dumps(localizer_job.get_output_ref("localized_file"))

def get_localizer_applet():
    """Return a dxpy.DXApplet object for the localizer applet."""

    # First try to find an existing applet.
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
    """Build the file localizer applet on dnanexus."""

    dx_applet_id = dxpy.api.applet_new({
        "name": APPLET_NAME,
        "title": "WES URL Localizer",
        "dxapi": dxpy.API_VERSION,
        "project": dxpy.PROJECT_CONTEXT_ID,
        "properties": {"version": APPLET_VERSION},
        "inputSpec": [
            {
                "name": "url",
                "class": "string"
            },
            {
                "name": "project",
                "class": "string"
            },
            {
                "name": "folder",
                "class": "string"
            }
        ],
        "outputSpec": [
            {
                "name": "localized_file",
                "class": "file"
            }
        ],
        "runSpec": {
            "code": APPLET_CODE,
            "interpreter": "python2.7",
            "systemRequirements": {
                "*": {"instanceType": "mem1_ssd1_x2"}},
            "execDepends": [
                {
                    "name": "google-cloud-storage",
                    "package_manager": "pip"
                }
            ]
        },
        "access": {
            "network": ["*"],
            "project": "UPLOAD"
        },
        "release": "14.04"
    })

    return dxpy.DXApplet(dx_applet_id["id"])

# Just for testing
if __name__ == "__main__":
    localize_file(sys.argv[1], sys.argv[2], sys.argv[3])
