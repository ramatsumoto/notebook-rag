from vertexai import rag
import vertexai
from msal import PublicClientApplication
import requests
import os
import json
from dotenv import load_dotenv
from google.cloud import storage
from google.cloud.storage.bucket import Bucket

load_dotenv()

class Page:
    def __init__(self, name, id, url, html):
        self.name = name
        self.id = id
        self.url = url
        self.html = html

def read_notebook_pages(get_html = True) -> list[Page]:
    # https://github.com/AzureAD/microsoft-authentication-library-for-python?tab=readme-ov-file
    app = PublicClientApplication(
        os.environ["ENTRA_APP_ID"],
        authority=f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}"
    )
    result = None
    
    result = app.acquire_token_interactive(
        scopes=["User.Read", "Notes.Read", "Notes.Read.All"]
    )
    
    if "access_token" not in result:
        print(result.get("error"))
        print(result.get("error_description"))
        print(result.get("correlation_id"))
        exit(1)

    print("Fetching pages...")

    notebook = list()

    # Read all page IDs + names (section name - page name)
    # https://stackoverflow.com/questions/28326800/odata-combining-expand-and-select
    url = f"https://graph.microsoft.com/v1.0/me/onenote/pages?$expand=parentNotebook($select=id; $filter=id eq '{os.environ['NOTEBOOK_ID']}'),parentSection($select=displayName)&$select=id,title,links,parentNotebook,parentSection"
    while True:
        graph_data = requests.get(
            url,
            headers={'Authorization': 'Bearer ' + result["access_token"]}
        ).json()
        if "value" not in graph_data:
            print("Empty response")
            print(url)
            break
        for page in graph_data["value"]:
            # Skip pages not in Notebook
            if page["parentNotebook"]["id"] != os.environ["NOTEBOOK_ID"]:
                continue
            name = f'{page["parentSection"]["displayName"]} - {page["title"]}'.replace("/", "-")

            if get_html:
                html = requests.get(
                    f"https://graph.microsoft.com/v1.0/me/onenote/pages/{page['id']}/content",
                    headers={'Authorization': 'Bearer ' + result["access_token"]}
                ).text
            else:
                html = ""

            notebook.append(Page(name, page["id"], page["links"]["oneNoteWebUrl"]["href"], html))
            print(f"Found page '{name}' ({page['id']}).")
        if "@odata.nextLink" in graph_data: # Pagination
            url = graph_data["@odata.nextLink"]
        else:
            break
    print(f"Found {len(notebook)} pages.")

    return notebook

def create_corpus():
    notebook = read_notebook_pages()

    directory = "./documents"
    location = "us-east4"
    vertexai.init(project=os.environ["PROJECT_ID"], location=location)

    embedding_model_config = rag.RagEmbeddingModelConfig(
        vertex_prediction_endpoint=rag.VertexPredictionEndpoint(
            publisher_model="publishers/google/models/text-embedding-005"
        )
    )
    rag_corpus = rag.create_corpus(
        display_name=os.environ["CORPUS_NAME"],
        backend_config=rag.RagVectorDbConfig(
            rag_embedding_model_config=embedding_model_config
        ),
    )
    print(f"Creating new corpus '{os.environ['CORPUS_NAME']}'")

    os.makedirs(directory, exist_ok=True)
    for page in notebook:
        print(f"Uploading '{page.name}'")
        path = f"{directory}/{page.name}.html"
        with open(path, "w") as html:
            html.write(page.html)
        
        rag.upload_file(
            rag_corpus.name,
            path,
            transformation_config=rag.TransformationConfig(
                chunking_config=rag.ChunkingConfig(
                    chunk_size=512,
                    chunk_overlap=100,
                )
            ),
            description=page.url,
            display_name=page.name
        )
    print(f"Finished creating corpus '{rag_corpus.name}'")

def upload_reference_data():
    notebook = read_notebook_pages(False)

    url_map = dict((page.name, page.url) for page in notebook)
    text = json.dumps(url_map)

    print(f"Connecting to Cloud bucket '{os.environ['BUCKET_NAME']}'")
    client = storage.Client()
    bucket = client.bucket(os.environ["BUCKET_NAME"])
    print(f"Updating URL map '{os.environ['URL_MAP_NAME']}'")
    bucket.delete_blobs([os.environ["URL_MAP_NAME"]], on_error=...)
    blob = bucket.blob(os.environ["URL_MAP_NAME"])
    blob.upload_from_string(text, content_type="application/json")
    print("Updated URL map")

if __name__ == "__main__":
    # for page in read_notebook_pages(False):
    #     print(page.name)
    #     print(page.url)
    client = storage.Client()
    bucket = client.bucket(os.environ["BUCKET_NAME"])
    url_map = bucket.get_blob(os.environ["URL_MAP_NAME"])
    text = url_map.download_as_text()
    notebook = json.loads(text)
    for k in notebook:
        print(f"{notebook[k]}\n")