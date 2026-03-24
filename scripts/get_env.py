import json, requests
cfg = json.load(open("eolink_config.json"))

# 试法1：get_customizeList 用不同参数
r = requests.post(
    cfg["space_url"] + "/api/v2/api_studio/management/global_source/env/get",
    headers={"Eo-Secret-Key": cfg["Eo-Secret-Key"], "Content-Type": "application/json"},
    json={
        "space_id": cfg["space_id"],
        "project_hash": cfg["project_id"],
        "page": 1,
        "pageSize": 20
    },
    timeout=15
)
print("试法1:", json.dumps(r.json(), ensure_ascii=False, indent=2))
