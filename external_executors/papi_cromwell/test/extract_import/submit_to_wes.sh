http POST "$WES_URL"/workflows workflow_descriptor=@hello_world_import.b64 workflow_params:=@inputs.json workflow_url=entry_point.wdl X-API-KEY:"$API_KEY"
