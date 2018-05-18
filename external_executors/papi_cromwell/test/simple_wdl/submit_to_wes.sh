http POST "$WES_URL"/workflows workflow_descriptor=@hello_world.wdl workflow_params:=@inputs.json X-API-KEY:"$API_KEY"
