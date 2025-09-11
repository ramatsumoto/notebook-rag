from google import genai
from google.genai import types
from vertexai import rag
import vertexai
import streamlit
from msal import PublicClientApplication
import requests
import os
from dotenv import load_dotenv

class Page:
    def __init__(self, name, id, url):
        self.name = name
        self.id = id
        self.url = url

load_dotenv()
location = "us-east4"

@streamlit.dialog("Allow access", dismissible=False)
def load_notebook(): 
    app = PublicClientApplication(
        os.environ["ENTRA_APP_ID"],
        authority=f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}"
    )
    result = None

    flow = app.initiate_device_flow(
        scopes=["User.Read", "Notes.Read", "Notes.Read.All"]
    )
    if "user_code" not in flow:
        print(flow)
        streamlit.error(flow)

    streamlit.write(flow["message"])

    result = app.acquire_token_by_device_flow(flow)
    
    if "access_token" not in result:
        print(result.get("error"))
        print(result.get("error_description"))
        print(result.get("correlation_id"))
        exit(1)

    streamlit.write("Authenticated! Now fetching pages...")

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

            notebook.append(Page(name, page["id"], page["links"]["oneNoteWebUrl"]["href"]))
            print(f"Found page '{name}' ({page['id']}).")
        if "@odata.nextLink" in graph_data: # Pagination
            url = graph_data["@odata.nextLink"]
        else:
            break
    streamlit.write(f"Found {len(notebook)} pages.")

    return notebook

if "notebook" not in streamlit.session_state:
    with streamlit.spinner("Accessing Notebook..."):
        streamlit.session_state.notebook = load_notebook()
        streamlit.rerun()

def convert_title_to_notebook_link(title):
    page = next((page for page in streamlit.session_state.notebook if page.name == title), None)
    if page is None:
        for page in streamlit.session_state.notebook:
            if title.removesuffix("xa0") in page.title:
                return f"[{page.name}]({page.url})"
        return ""
    return f"[{page.name}]({page.url})"

if "chat" not in streamlit.session_state:
    vertexai.init(project=os.environ["PROJECT_ID"], location=location)
    # Name of the newest corpus (named correctly)
    corpus_name = sorted(
        filter(lambda corpus: corpus.display_name == os.environ["CORPUS_NAME"], rag.list_corpora()), 
        key=lambda corpus: corpus.create_time
    )[-1].name
    print(f"Using corpus '{corpus_name}'")

    rag_retrieval_tool = types.Tool(
        retrieval=types.Retrieval(
            vertex_rag_store=types.VertexRagStore(
                rag_resources=[
                    types.VertexRagStoreRagResource(
                        rag_corpus=corpus_name
                    )
                ],
                rag_retrieval_config=types.RagRetrievalConfig(
                    top_k=3,
                    filter=types.RagRetrievalConfigFilter(
                        vector_distance_threshold=0.5
                    )
                ),
            ),
        ),
    )

    client = genai.Client(
        vertexai=True,
        project=os.environ["PROJECT_ID"],
        location=location,
    )

    chat = client.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            system_instruction="Keep responses brief unless told otherwise. Use at most 4 sentences.",
            tools=[rag_retrieval_tool],
        )
    )
    streamlit.session_state.chat = chat
    streamlit.session_state.tooltips = list()

for message, tooltip in zip(streamlit.session_state.chat.get_history(), streamlit.session_state.tooltips):
    role = "assistant" if message.role == "model" else message.role
    with streamlit.chat_message(role):
        streamlit.markdown("\n".join(part.text for part in message.parts), help=tooltip if role == "assistant" else None)

if prompt := streamlit.chat_input("Ask something"):
    with streamlit.chat_message("user"):
        streamlit.markdown(prompt)
        streamlit.session_state.tooltips.append("")

    with streamlit.spinner("Thinking..."):
        response = streamlit.session_state.chat.send_message(prompt)
        if response.candidates and response.candidates[0].grounding_metadata and response.candidates[0].grounding_metadata.grounding_chunks:
            retrieved = set(chunk.retrieved_context.title for chunk in response.candidates[0].grounding_metadata.grounding_chunks if chunk is not None)
            sources_tooltip = "\n".join(f"* {convert_title_to_notebook_link(title)}" for title in retrieved)
        else:
            sources_tooltip = None

    with streamlit.chat_message("assistant"):
        streamlit.markdown(response.text, help=sources_tooltip)
        streamlit.session_state.tooltips.append(sources_tooltip)