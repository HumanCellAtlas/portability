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

import cgi
import datetime
import io
import json
import os
import string
import uuid

import boto3
from boto3.dynamodb.conditions import Key
from requests_toolbelt import MultipartDecoder

# This is the Name tag that instances launched by these lambdas will have
INSTANCE_NAME_TAG = "local-cromwell-portability-test"

# The standard Amazon Linux AMI. This will break outside of us-east-1, and
# probably for certain instance types too.
AMI = "ami-55ef662f"

# These are placed in the environment by the Cloudformation template. Rather
# than sticking os.environs all over the script, put all the Cloudformation
# variables here so I stop forgetting about them.
CLOUDFORMATION_VARIABLES = {
    "bucket": os.environ.get("BUCKET"),
    "iam_profile": os.environ.get("IAM_PROFILE"),
    "instance_type": os.environ.get("INSTANCE_TYPE"),
    "region": os.environ.get("REGION"),
    "state_table": os.environ.get("STATE_TABLE"),
    "subnet": os.environ.get("SUBNET"),
    "timing_table": os.environ.get("TIMING_TABLE"),
    "volume_size": int(os.environ.get("VOLUME_SIZE", 0))
}


##############################################################################
# /runs POST
##############################################################################
def decode_runs_post_form(event):
    """Convert the multipart form that comes with a POST to /runs into a
    dictionary.
    """

    # Decode the form data
    content_type = event["headers"].get("Content-Type") or event["headers"].get("content-type")
    decoder = MultipartDecoder(event["body"].encode(), content_type)
    decoded_body = {}

    for part in decoder.parts:
        # parts.headers doesn't seems to support key lookup??? So just iterate
        # through the values, there are usually two
        for key, value in part.headers.items():
            if key == b"Content-Disposition":
                content_disposition = cgi.parse_header(value.decode())
                name = content_disposition[1]["name"]
                filename = content_disposition[1].get("filename")
                break

        # I guess it's okay to fail if content-disposition doesn't have a name
        # I think that means the form isn't right
        if name == "workflow_attachment":
            decoded_body.setdefault("workflow_attachment", {})[filename] = part.text
        else:
            decoded_body[name] = part.text

    return decoded_body

def wes_runs_post(event, context):
    """Handle a POST to /runs."""

    run_id = str(uuid.uuid4())

    ddb = boto3.resource("dynamodb", region_name=CLOUDFORMATION_VARIABLES["region"])
    state_table = ddb.Table(CLOUDFORMATION_VARIABLES["state_table"])
    timing_table = ddb.Table(CLOUDFORMATION_VARIABLES["timing_table"])
    state_table.put_item(
        Item={
            "RunId": run_id,
            "WESState": "INITIALIZING"
        }
    )
    timing_table.put_item(
        Item={
            "RunId": run_id,
            "StartTime": "{:%Y-%m-%dT%H:%M:%SZ}".format(datetime.datetime.utcnow()),
            "EndTime": "empty"
            }
    )

    ec2_client = boto3.client('ec2', region_name=CLOUDFORMATION_VARIABLES["region"])
    bucket = boto3.resource("s3").Bucket(CLOUDFORMATION_VARIABLES["bucket"])

    decoded_body = decode_runs_post_form(event)


    # Serialize workflow request to S3
    for filename, file_contents in decoded_body.get("workflow_attachment", {}).items():
        if filename == decoded_body["workflow_url"]:
            attachment_type = "entrypoint"
        else:
            attachment_type = "dependency"

        key = os.path.join(run_id, "workflow_attachments", filename)
        bucket.upload_fileobj(
            io.BytesIO(file_contents.encode()),
            key,
            ExtraArgs={"Metadata": {"attachment_type": attachment_type}})

    workflow_params_key = os.path.join(run_id, "workflow_params.json")
    bucket.upload_fileobj(io.BytesIO(decoded_body["workflow_params"].encode()),
                          workflow_params_key)

    workflow_url_key = os.path.join(run_id, "workflow_url")
    bucket.upload_fileobj(io.BytesIO(decoded_body.get("workflow_url", "").encode()),
                          workflow_url_key)

    workflow_type_key = os.path.join(run_id, "workflow_type")
    bucket.upload_fileobj(io.BytesIO(decoded_body["workflow_type"].encode()),
                          workflow_type_key)

    workflow_type_version_key = os.path.join(run_id, "workflow_type_version")
    bucket.upload_fileobj(io.BytesIO(decoded_body["workflow_type_version"].encode()),
                          workflow_type_version_key)

    user_data_template = string.Template(open(os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "user_data_template.bash")).read())

    # Prepare the UserData script to pass to the new EC2 instance.
    user_data = user_data_template.safe_substitute(
        RUN_ID=run_id,
        STATE_TABLE=CLOUDFORMATION_VARIABLES["state_table"],
        TIMING_TABLE=CLOUDFORMATION_VARIABLES["timing_table"],
        REGION=CLOUDFORMATION_VARIABLES["region"],
        BUCKET=CLOUDFORMATION_VARIABLES["bucket"])

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
                {"Key": "Name", "Value": INSTANCE_NAME_TAG},
                {"Key": "RunId", "Value": run_id}
            ]}
        ],
        IamInstanceProfile={
            "Arn": CLOUDFORMATION_VARIABLES["iam_profile"]
        }
    )

    return {"statusCode": "200",
            "body": json.dumps({"run_id": run_id})}



##############################################################################
# /service-info GET
##############################################################################
def wes_service_info_get(event, context):
    """Handle a GET to /service-info."""

    service_info = {
        "workflow_type_versions": {
            "WDL": {
                "workflow_type_version": [
                    "1.0",
                    "draft-3"
                ]
            }
        },
        "supported_wes_versions": [
            "0.3.0"
        ],
        "supported_filesystem_protocols": [
            "gs",
            "s3",
            "http",
            "https"
        ],
        "workflow_engine_versions": {
            "cromwell": "36"
        },
        "system_state_counts": {},
        "auth_instructions_url": "docs.aws.amazon.com", # Good luck!
        "contact_info": "hca@humancellatlas.org",
        "tags": {}
    }

    return {
        "statusCode": "200",
        "body": json.dumps(service_info)
    }

##############################################################################
# /runs/{run_id} DELETE
##############################################################################
def wes_runs_runid_delete(event, context):

    run_id = event["pathParameters"]["run_id"]
    ec2 = boto3.resource("ec2")
    ddb = boto3.resource("dynamodb", region_name=CLOUDFORMATION_VARIABLES["region"])
    found_instances = list(ec2.instances.filter(
        Filters=[{"Name": "tag:Name", "Values": [INSTANCE_NAME_TAG]},
                 {"Name": "tag:RunId", "Values": [run_id]}]))

    if not found_instances:
        return {
            "statusCode": "404",
            "body": json.dumps({
                "msg": "Run {} not found".format(run_id),
                "status_code": "404"
            })
        }

    if len(found_instances) > 1:
        return {
            "statusCode": "500",
            "body": json.dumps({
                "msg": "Multipe runs with id {} found. This is not good.".format(run_id),
                "status_code": "500"
            })
        }

    instance_to_terminate = found_instances[0]

    if instance_to_terminate.state["Name"] not in ("pending", "running"):
        return {
            "statusCode": "401",
            "body": json.dumps({
                "msg": "What's done is done.",
                "status_code": "401"
            })
        }

    response = instance_to_terminate.terminate()

    state_table = ddb.Table(CLOUDFORMATION_VARIABLES["state_table"])
    state_table.update_item(
        Key={"RunId": run_id},
        UpdateExpression="set WESState = :s",
        ExpressionAttributeValues={":s": "CANCELED"}
    )
    timing_table = ddb.Table(CLOUDFORMATION_VARIABLES["timing_table"])
    timing_table.update_item(
        Key={"RunId": run_id},
        UpdateExpression="set EndTime = :s",
        ExpressionAttributeValues={":s": "{:%Y-%m-%dT%H:%M:%SZ}".format(datetime.datetime.utcnow())}
    )

    return {
        "statusCode": "200",
        "body": json.dumps({"run_id": run_id})
    }

##############################################################################
# /runs/{run_id} GET
##############################################################################
def wes_runs_runid_get(event, context):

    run_id = event["pathParameters"]["run_id"]
    ddb = boto3.resource("dynamodb", region_name=CLOUDFORMATION_VARIABLES["region"])
    bucket = boto3.resource("s3").Bucket(CLOUDFORMATION_VARIABLES["bucket"])
    state_table = ddb.Table(CLOUDFORMATION_VARIABLES["state_table"])
    timing_table = ddb.Table(CLOUDFORMATION_VARIABLES["timing_table"])

    # Read the tables for state and timing info
    state_response = state_table.query(KeyConditionExpression=Key("RunId").eq(run_id))
    if state_response["Count"] == 0:
        return {
            "statusCode": "404",
            "body": json.dumps({
                "msg": "Run id {} not found".format(run_id),
                "status_code": "404"
            })
        }
    wes_state = state_response["Items"][0]["WESState"]

    timing_response = timing_table.query(KeyConditionExpression=Key("RunId").eq(run_id))
    try:
        start_time = timing_response["Items"][0].get("StartTime", "")
        end_time = timing_response["Items"][0].get("EndTime", "")
    except IndexError:
        start_time, end_time = "", ""

    # Get request information in S3
    s3_client = boto3.client("s3")
    s3 = boto3.resource("s3")
    def _read_s3_obj(obj):
        buf = io.BytesIO()
        obj.download_fileobj(buf)
        buf.seek(0)
        return buf.read().decode()

    def _get_s3_url(obj):
        return s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": obj.bucket_name, "Key": obj.key})

    workflow_params = _read_s3_obj(bucket.Object(os.path.join(run_id, "workflow_params.json")))
    workflow_type = _read_s3_obj(bucket.Object(os.path.join(run_id, "workflow_type")))
    workflow_type_version = _read_s3_obj(bucket.Object(os.path.join(run_id, "workflow_type_version")))
    workflow_url = _read_s3_obj(bucket.Object(os.path.join(run_id, "workflow_url")))

    # Walk the logs prefix
    run_stdout, run_stderr = "", ""
    task_logs = []
    logs_prefix = os.path.join(run_id, "logs")
    for obj_summary in bucket.objects.filter(Prefix=os.path.join(run_id, "logs")).all():
        if obj_summary.key == os.path.join(logs_prefix, "cromwell-stdout.log"):
            run_stdout = _get_s3_url(obj_summary)
        elif obj_summary.key == os.path.join(logs_prefix, "cromwell-stderr.log"):
            run_stderr = _get_s3_url(obj_summary)
        elif os.path.basename(obj_summary.key) == "stdout":

            task_stdout = _get_s3_url(obj_summary)
            task_stderr = _get_s3_url(s3.ObjectSummary(
                obj_summary.bucket_name,
                os.path.join(os.path.dirname(obj_summary.key), "stderr")))
            task_rc = _read_s3_obj(s3.Object(
                obj_summary.bucket_name,
                os.path.join(os.path.dirname(obj_summary.key), "rc")))
            task_name = os.path.dirname(obj_summary.key)[len(logs_prefix):]

            task_logs.append(
                {
                    "name": task_name,
                    "cmd": [],
                    "start_time": "",
                    "end_time": "",
                    "stdout": task_stdout,
                    "stderr": task_stderr,
                    "exit_code": task_rc
                }
            )

    response = {
        "run_id": run_id,
        "request": {
            "workflow_params": json.loads(workflow_params),
            "workflow_type": workflow_type,
            "workflow_type_version": workflow_type_version,
            "tags": {},
            "workflow_engine_parameters": {},
            "workflow_url": workflow_url
        },
        "state": wes_state,
        "run_log": {
            "name": "",
            "exit_code": "",
            "cmd": [],
            "start_time": start_time,
            "end_time": end_time,
            "stdout": run_stdout,
            "stderr": run_stderr
        },
        "task_logs": task_logs,
        "outputs": {}
    }

    return {
        "statusCode": "200",
        "body": json.dumps(response)
    }

##############################################################################
# /runs/{run_id}/status GET
##############################################################################
def wes_runs_runid_status_get(event, context):

    run_id = event["pathParameters"]["run_id"]
    ddb = boto3.resource("dynamodb", region_name=CLOUDFORMATION_VARIABLES["region"])
    state_table = ddb.Table(CLOUDFORMATION_VARIABLES["state_table"])

    # Read the tables for state and timing info
    state_response = state_table.query(KeyConditionExpression=Key("RunId").eq(run_id))
    if state_response["Count"] == 0:
        return {
            "statusCode": "404",
            "body": json.dumps({
                "msg": "Run id {} not found".format(run_id),
                "status_code": "404"
            })
        }
    wes_state = state_response["Items"][0]["WESState"]

    return {
        "statusCode": "200",
        "body": json.dumps({
            "run_id": run_id,
            "state": wes_state
        })
    }

##############################################################################
# /runs GET
##############################################################################
def wes_runs_get(event, context):
    
    page_size = int((event.get("queryStringParameters") or {}).get("page_size", 20))
    page_size = min(page_size, 50)
    page_token = (event.get("queryStringParameters") or {}).get("page_token")

    ddb_client = boto3.client('dynamodb')
    paginator = ddb_client.get_paginator("scan")

    pagination_kwargs = {
        "TableName": CLOUDFORMATION_VARIABLES["state_table"],
        "PaginationConfig": {"PageSize": page_size, "MaxItems": page_size}
    }
    if page_token:
        pagination_kwargs["PaginationConfig"]["StartingToken"] = page_token

    pages = paginator.paginate(**pagination_kwargs)
    page = list(pages)[0]

    runs = [{"run_id": i["RunId"]["S"], "state": i["WESState"]["S"]} for i in page["Items"]]

    return {
        "statusCode": "200",
        "body": json.dumps({
            "runs": runs,
            "next_page_token": pages.resume_token
        })
    }
