import os
import requests
import base64
import mimetypes

def build_attachments_from_paths(paths):
    """
    Convert local file paths into the JSON format expected by /api/send_email/.
    Returns:
      [
        {"name": "file.pdf", "content_base64": "...", "content_type": "application/pdf"},
        ...
      ]
    """
    out = []
    for p in paths:
        guessed_type, _ = mimetypes.guess_type(p)
        content_type = guessed_type or "application/octet-stream"
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        out.append(
            {"name": os.path.basename(p), "content_base64": b64, "content_type": content_type}
        )
    return out

def send_email(subject, emails, body, attachments=None):
    """
    attachments (optional):
      - already prepared list of dicts (see formats above), OR
      - list of file paths -> pass build_attachments_from_paths(paths)
    """
    if not os.getenv("DEPLOYMENT_ENVIRONMENT"):
        return

    url = f"https://{os.getenv('DOMAIN_BASE')}/api/send_email/"
    headers = {
        "Authorization": f"Api-Key {os.getenv('LEX_API_KEY')}",
        "Content-Type": "application/json",
    }

    data = {"subject": subject, "emails": emails, "body": body}

    if attachments:
        data["attachments"] = attachments

    response = requests.post(url, json=data, headers=headers, timeout=20)

    if response.status_code == 200:
        return response.json()

    print("Failed with status code:", response.status_code)
    print("Response:", response.text)
    raise Exception("Failed to send email")

def get_client_roles():
    url = f"https://{os.getenv('DOMAIN_BASE')}/api/get_client_roles/"
    headers = {
        'Authorization': f"Api-Key {os.getenv('LEX_API_KEY')}",
        'Content-Type': 'application/json'
    }
    data = {
        'keycloak_client_id': os.getenv('KEYCLOAK_INTERNAL_CLIENT_ID')
    }

    response = requests.get(url, params=data, headers=headers)
    if response.status_code == 200:
        print(response.json())
        return response.json()
    else:
        print('Failed with status code:', response.status_code)
        print('Response:', response.text)
        raise Exception("Failed to get client roles")