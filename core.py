import datetime
import psycopg2
import requests
import time

import config


def get_token(user, password, domain_id=None, project_name=None):
    if domain_id is None:
        domain_id = "default"
    token_data = {
        "auth": {
            "identity": {
                "methods": ["password"],
                "password": {
                    "user": {
                        "name": user,
                        "domain": {"id": domain_id},
                        "password": password
                    }
                }
            }
        }
    }
    if project_name is not None:
        token_data["auth"]["scope"] = {
            "project": {
                "name": project_name,
                "domain": {"id": domain_id}
            }
        }
    result = dict()
    try:
        keystone_response = requests.post(url=f"http://{config.openstack_ip}/identity/v3/auth/tokens?nocatalog",
                                          json=token_data)
    except requests.exceptions.ConnectionError:
        result["error"] = f"keystone is not available."
    else:
        if keystone_response.status_code != 201:
            result["error"] = f"keystone error: {keystone_response.text}"
        else:
            result["token"] = keystone_response.headers["X-Subject-Token"]
    return result


def get_flavors(token):
    response = requests.get(url=f"http://{config.openstack_ip}/compute/v2.1/flavors/detail",
                            headers={"X-Auth-Token": token})
    return {"status": response.status_code, "data": response.json()}


def get_images(token):
    response = requests.get(url=f"http://{config.openstack_ip}/image/v2/images", headers={"X-Auth-Token": token})
    return {"status": response.status_code, "data": response.json()}


def get_networks(token):
    response = requests.get(url=f"http://{config.openstack_ip}:9696/v2.0/networks", headers={"X-Auth-Token": token})
    return {"status": response.status_code, "data": response.json()}


def get_virtual_machines(token):
    response = requests.get(url=f"http://{config.openstack_ip}/compute/v2.1/servers/detail",
                            headers={"X-Auth-Token": token})
    return {"status": response.status_code, "data": response.json()}


def get_resource_id_by_name(token, resource_name, resource_type, function_to_get_resources):
    all_resources = globals()[function_to_get_resources](token)
    if all_resources["status"] != 200:
        return {"status": all_resources["status"], "error": f"get resource list failed: {all_resources['error']}"}
    for item in all_resources["data"][resource_type]:
        if item["name"] == resource_name:
            return item["id"]
    return {"status": 404, "error": f"resource {resource_name} not found"}


def create_virtual_machine(token, flavor_name, image_name, network_id, virtual_machine_name):
    request_data = {
        "server": {
            "name": virtual_machine_name,
            "min_count": 1,
            "max_count": 1,
            "networks": [{"uuid": network_id}]
            }
        }
    for ref, ref_data in {
        "flavorRef": {"resource_name": flavor_name, "resource_type": "flavors",
                      "function_to_get_resources": "get_flavors"},
        "imageRef": {"resource_name": image_name, "resource_type": "images",
                     "function_to_get_resources": "get_images"}}.items():
        service_response = get_resource_id_by_name(token, **ref_data)
        if "error" in service_response:
            return service_response
        request_data["server"][ref] = service_response
    response = requests.post(url=f"http://{config.openstack_ip}/compute/v2.1/servers",
                             headers={"X-Auth-Token": token},
                             json=request_data
                             )
    return {"status": response.status_code, "data": response.json()}


def get_virtual_machines_number():
    token = get_token(user=config.service_openstack_user,
                      password=config.service_openstack_password,
                      domain_id=config.service_openstack_domain_id,
                      project_name=config.service_openstack_project_name)
    if "error" in token:
        return token
    vms_data = get_virtual_machines(token=token["token"])
    if vms_data["status"] != 200:
        return {"status": vms_data["status"], "error": f"couldn't get server list: {vms_data['data']}"}
    return len(vms_data['data']["servers"])


def send_statistics():
    try:
        connection = psycopg2.connect(
            f"dbname={config.database_name} user={config.database_user} password={config.database_password}")
        cursor = connection.cursor()
        while True:
            vm_number = get_virtual_machines_number()
            if not isinstance(vm_number, int):
                print(vm_number)
                continue
            cursor.execute(
                "INSERT INTO statistics (date_accurate_to_the_hour, virtual_machines_number) "
                "VALUES (%s, %s)",
                (datetime.datetime.utcnow().replace(minute=0, second=0, microsecond=0), vm_number))
            connection.commit()
            time.sleep(config.statistics_frequency_in_sec)
    except psycopg2.errors.UndefinedTable:
        print("SQL table does not exist")
    except psycopg2.errors.UniqueViolation:
        print("statistics for the hour was already recorded")
