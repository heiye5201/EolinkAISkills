import json, requests
cfg = json.load(open("eolink_config.json"))
r = requests.post(
    cfg["space_url"] + "/api/v2/api_studio/management/project/get_customizeList",
    headers={"Eo-Secret-Key": cfg["Eo-Secret-Key"], "Content-Type": "application/json"},
    json={"space_id": cfg["space_id"], "project_id": cfg["project_id"]},
    timeout=15
)
print(json.dumps(r.json(), ensure_ascii=False, indent=2))

