import requests
from bs4 import BeautifulSoup

url = "https://www.python.org/downloads/"
response = requests.get(url)
soup = BeautifulSoup(response.content, 'html.parser')

container = soup.find("ol", {"class": "list-row-container menu"})
if container:
    releases = container.find_all("li")
    security_found = False
    for release in releases:
        version = release.find("span", {"class": "release-version"})
        status = release.find("span", {"class": "release-status"})

        if version and status:
            if status.text.strip() == "security" and not security_found:
                print("Python version:", version.text.strip())
                print("Maintenance status:", status.text.strip())
                print()
                security_found = True
            elif status.text.strip() in ["prerelease", "bugfix"]:
                print("Python version:", version.text.strip())
                print("Maintenance status:", status.text.strip())
                print()
else:
    print("No data found.")
