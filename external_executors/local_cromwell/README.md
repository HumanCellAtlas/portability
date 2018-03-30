# Local Cromwell Service

This deploys an API on AWS that looks a little bit like WES and runs WDLs using
Cromwell with a local backend. Its purpose is to provide one portability test
environment for the HCA's secondary analysis workflows.

There are two files:

### local_cromwell.py
This defines three Lambda functions that are responsible for handling three
request types:

- POST `/workflows`
- GET `/workflows/{workflow_id}/status`
- GET `/workflows/{workflow_id}`

A POST to `/workflows` is handled by the `workflows_post` function. That
launches an EC2 instance that will run Cromwell and record outcomes in a
database. A GET to `/workflows/{workflow_id}/status` is handled by the
`workflow_status` function. That queries the database for events associated
with the `workflow_id` and reports its status using WES terms. A GET to
`/workflows/{workflow_id}` is handled by the `workflow_log` function and
reports status along with logging information.

### local_cromwell_template.json
This is an AWS Cloudformation template for the local Cromwell service. It
deploys an API to a "test" stage that has the three endpoints mentioned above.
The template handles all the AWS concerns like setting up roles, policies,
tables, functions, etc. The template contains a definition of the API using
Swagger.

## Deploying
There are two steps to deploying the service:
1. Zip and upload the Lambda functions. Just `zip local_cromwell.zip
   local_cromwell.py` and upload the zip file to an S3 bucket.
2. Use AWS Cloudformation to create a stack using
   `local_cromwell_template.json`. This can be done through the AWS console.
   You will have to supply the bucket and file name for the
   `local_cromwell.zip` file that you uploaded.

This can be done with the deploy.sh script:
```
sh deploy.sh <stack_name> <bucket_name>
```

## Limitations
The jobs have a hard-coded 90-minute time limit. There does appear to be a
corner case where jobs will hang, so this attemps to avoid that.

## Example Output

A GET to `/workflows/{workflow_id}` returns logs for the overall workflow
produced by Cromwell, as well as task-level logs from the stdout and stderr
files in their respective execution directories. A simple response would look
like this:

```
{
    "state": "Complete",
    "task_logs": [
        {
            "exit_code": 0,
            "name":
"CountLinesWorkflow/7068639b-7225-464f-b1e8-052b49a602b3/call-CountLines/execution",
            "stderr": "And something in stderr!\n\n\n",
            "stdout": "Oh hey something in stdout!\n"
        }
    ],
    "workflow_id": "326f1ca3-b3ce-4281-8dc5-028857e72a46",
    "workflow_log": {
        "end_time": "2018-03-30T21:36:22Z",
        "exit_code": "0",
        "start_time": "2018-03-30T21:35:59Z",
        "stderr": "",
        "stdout": "[2018-03-30 21:36:02,25] [info] Running with database db.url = jdbc:hsqldb:mem:053244c2-1a31-4c38-8512-14e63920391d;shutdown=false;hsqldb.tx=mvcc\n[2018-03-30 21:36:07,74] [info] Running migration RenameWorkflowOptionsInMetadata with a read batch size of 100000 and a write batch size of 100000\n[2018-03-30 21:36:07,75] [info] [RenameWorkflowOptionsInMetadata] 100%\n[2018-03-30 21:36:07,87] [info] Running with database db.url = jdbc:hsqldb:mem:f3555b67-8005-444f-b1f5-8a048a254086;shutdown=false;hsqldb.tx=mvcc\n[2018-03-30 21:36:08,33] [info] Slf4jLogger started\n[2018-03-30 21:36:08,64] [info] Metadata summary refreshing every 2 seconds.\n[2018-03-30 21:36:08,66] [info] CallCacheWriteActor configured to write to the database with batch size 100 and flush rate 3 seconds.\n[2018-03-30 21:36:08,67] [info] WriteMetadataActor configured to write to the database with batch size 200 and flush rate 5 seconds.\n[2018-03-30 21:36:08,70] [info] Starting health monitor with the following checks: DockerHub, Engine Database\n[2018-03-30 21:36:09,54] [info] SingleWorkflowRunnerActor: Submitting workflow\n[2018-03-30 21:36:09,60] [info] Workflow 7068639b-7225-464f-b1e8-052b49a602b3 submitted.\n[2018-03-30 21:36:09,60] [info] SingleWorkflowRunnerActor: Workflow submitted \u001b[38;5;2m7068639b-7225-464f-b1e8-052b49a602b3\u001b[0m\n[2018-03-30 21:36:09,60] [info] 1 new workflows fetched\n[2018-03-30 21:36:09,60] [info] WorkflowManagerActor Starting workflow \u001b[38;5;2m7068639b-7225-464f-b1e8-052b49a602b3\u001b[0m\n[2018-03-30 21:36:09,61] [info] WorkflowManagerActor Successfully started WorkflowActor-7068639b-7225-464f-b1e8-052b49a602b3\n[2018-03-30 21:36:09,61] [info] Retrieved 1 workflows from the WorkflowStoreActor\n[2018-03-30 21:36:10,42] [info] MaterializeWorkflowDescriptorActor [\u001b[38;5;2m7068639b\u001b[0m]: Call-to-Backend assignments: CountLinesWorkflow.CountLines -> Local\n[2018-03-30 21:36:12,60] [info] WorkflowExecutionActor-7068639b-7225-464f-b1e8-052b49a602b3 [\u001b[38;5;2m7068639b\u001b[0m]: Starting calls: CountLinesWorkflow.CountLines:NA:1\n[2018-03-30 21:36:12,73] [info] BackgroundConfigAsyncJobExecutionActor [\u001b[38;5;2m7068639b\u001b[0mCountLinesWorkflow.CountLines:NA:1]: \u001b[38;5;5m    echo \"Oh hey something in stdout!\"\n    >&2 echo \"And something in stderr!\n\n\"\n    wc -l /cromwell-executions/CountLinesWorkflow/7068639b-7225-464f-b1e8-052b49a602b3/call-CountLines/inputs/tmp/tmpf8kpw_37/gencode.v27.rRNA.interval_list | awk '{print $1}' > line.count\u001b[0m\n[2018-03-30 21:36:12,75] [info] BackgroundConfigAsyncJobExecutionActor [\u001b[38;5;2m7068639b\u001b[0mCountLinesWorkflow.CountLines:NA:1]: executing: /bin/bash /cromwell-executions/CountLinesWorkflow/7068639b-7225-464f-b1e8-052b49a602b3/call-CountLines/execution/script\n[2018-03-30 21:36:12,79] [info] BackgroundConfigAsyncJobExecutionActor [\u001b[38;5;2m7068639b\u001b[0mCountLinesWorkflow.CountLines:NA:1]: job id: 8840\n[2018-03-30 21:36:12,79] [info] BackgroundConfigAsyncJobExecutionActor [\u001b[38;5;2m7068639b\u001b[0mCountLinesWorkflow.CountLines:NA:1]: Status change from - to WaitingForReturnCodeFile\n[2018-03-30 21:36:16,51] [info] BackgroundConfigAsyncJobExecutionActor [\u001b[38;5;2m7068639b\u001b[0mCountLinesWorkflow.CountLines:NA:1]: Status change from WaitingForReturnCodeFile to Done\n[2018-03-30 21:36:17,60] [info] WorkflowExecutionActor-7068639b-7225-464f-b1e8-052b49a602b3 [\u001b[38;5;2m7068639b\u001b[0m]: Workflow CountLinesWorkflow complete. Final Outputs:\n{\n  \"CountLinesWorkflow.line_count\": 744\n}\n[2018-03-30 21:36:17,63] [info] WorkflowManagerActor WorkflowActor-7068639b-7225-464f-b1e8-052b49a602b3 is in a terminal state: WorkflowSucceededState\n[2018-03-30 21:36:21,51] [info] SingleWorkflowRunnerActor workflow finished with status 'Succeeded'.\n{\n  \"outputs\": {\n    \"CountLinesWorkflow.line_count\": 744\n  },\n  \"id\": \"7068639b-7225-464f-b1e8-052b49a602b3\"\n}\n[2018-03-30 21:36:21,57] [info] Message [cromwell.core.actor.StreamActorHelper$StreamFailed] without sender to Actor[akka://cromwell-system/deadLetters] was not delivered. [1] dead letters encountered. This logging can be turned off or adjusted with configuration settings 'akka.log-dead-letters' and 'akka.log-dead-letters-during-shutdown'.\n[2018-03-30 21:36:21,57] [info] Message [cromwell.core.actor.StreamActorHelper$StreamFailed] without sender to Actor[akka://cromwell-system/deadLetters] was not delivered. [2] dead letters encountered. This logging can be turned off or adjusted with configuration settings 'akka.log-dead-letters' and 'akka.log-dead-letters-during-shutdown'.\n"
    }
}
```
