#!/bin/bash
set -ex -o pipefail

# Set a maximum run time of 90 minutes to kill stalled jobs.
shutdown -H 90 &

yum update -qy
yum install -qy python36-pip docker java-1.8.0-openjdk-headless
service docker start
pip-3.6 -q install boto3 google-cloud-storage
wget -q https://github.com/broadinstitute/cromwell/releases/download/34/cromwell-34.jar

function update_status {
python3 << END
import boto3, datetime
ddb = boto3.resource("dynamodb", region_name="$REGION")
table = ddb.Table("$STATE_TABLE")
table.update_item(
    Key={"RunId": "$RUN_ID"},
    UpdateExpression="set WESState = :s",
    ExpressionAttributeValues={":s": "$1"}
)
END
}

function fail {
    set +e
    update_status "EXECUTOR_ERROR"
    shutdown -c || true
    shutdown -H 5
}

trap 'fail' EXIT

update_status "RUNNING"

# Download the workflow files and the workflow params
entrypoint=$(python3 << END
import boto3, os, pathlib
s3 = boto3.resource('s3')
bucket = s3.Bucket("$BUCKET")
wf_attachments_prefix = os.path.join("$RUN_ID", "workflow_attachments")
for obj_summary in bucket.objects.filter(Prefix=wf_attachments_prefix).all():
    obj = s3.Object(obj_summary.bucket_name, obj_summary.key)
    local_path = os.path.join(*obj.key.split("/")[2:])
    pathlib.Path(os.path.dirname(local_path)).mkdir(parents=True, exist_ok=True)
    bucket.download_file(obj.key, local_path)
    if obj.metadata["attachment_type"] == "entrypoint":
        print(local_path)
wf_params_key = os.path.join("$RUN_ID", "workflow_params.json")
bucket.download_file(wf_params_key, "workflow_params.json")
END
)


# Localize inputs
python3 << END
import json, os, subprocess, tempfile
from google.cloud import storage
inputs_dict = json.load(open("workflow_params.json"))

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
        return {k: localize_inputs(v, match_func, localize_func) for k, v in obj.items()}
    elif isinstance(obj, str):
        if match_func(obj):
            return localize_func(obj)
        else:
            return obj
    else:
        return obj

localized_inputs_dict = localize_inputs(inputs_dict, match_url, localize_url)
json.dump(localized_inputs_dict, open("localized_workflow_params.json", "w"))
END

set +e
java8 -jar cromwell-34.jar run "$entrypoint" --inputs localized_workflow_params.json > cromwell-stdout.log 2> cromwell-stderr.log
cromwell_rc=$?
set -e

python3 << END
import boto3, os
s3 = boto3.resource('s3')
bucket = s3.Bucket("$BUCKET")

# Cromwell logs
try:
    cromwell_stdout_key = os.path.join("$RUN_ID", "logs", "cromwell-stdout.log")
    bucket.upload_file("cromwell-stdout.log", cromwell_stdout_key)
except Exception as e:
    print(e)

try:
    cromwell_stderr_key = os.path.join("$RUN_ID", "logs", "cromwell-stderr.log")
    bucket.upload_file("cromwell-stderr.log", cromwell_stderr_key)
except Exception as e:
    print(e)

# Walk the cromwell execution directory to find tasks
for root, dirs, files in os.walk("/cromwell-executions/"):
    if "stdout" in files and "stderr" in files:

        task_name = root[len("/cromwell-executions/"):]

        try:
            stderr_path = os.path.join(root, "stderr")
            stderr_key = os.path.join("$RUN_ID", "logs", task_name, "stderr")
            bucket.upload_file(stderr_path, stderr_key)
        except Exception as exc:
            print(exc)

        try:
            stdout_path = os.path.join(root, "stdout")
            stdout_key = os.path.join("$RUN_ID", "logs", task_name, "stdout")
            bucket.upload_file(stdout_path, stdout_key)
        except Exception as exc:
            print(exc)

        try:
            rc_path = os.path.join(root, "rc")
            if not os.path.isfile(rc_path):
                with open(rc_path, "w") as rc_file:
                    rc_file.write("-1")
            rc_key = os.path.join("$RUN_ID", "logs", task_name, "rc")
            bucket.upload_file(rc_path, rc_key)
        except Exception as exc:
            print(exc)
END

if [ $cromwell_rc == 0 ]; then
    update_status "COMPLETE"
else
    update_status "EXECUTOR_ERROR"
fi

python3 << END
import boto3, datetime
ddb = boto3.resource("dynamodb", region_name="$REGION")
table = ddb.Table("$TIMING_TABLE")
table.update_item(
    Key={"RunId": "$RUN_ID"},
    UpdateExpression="set EndTime = :s",
    ExpressionAttributeValues={":s": "{:%Y-%m-%dT%H:%M:%SZ}".format(datetime.datetime.utcnow())}
)
END

# Turn off the trap or it records Failed twice.
trap - EXIT
shutdown -c || true
shutdown -H 5
