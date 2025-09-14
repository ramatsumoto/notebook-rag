from google import genai
from google.genai import types
from google.cloud import storage
from vertexai import rag
import vertexai
import streamlit
import os
import json
from dotenv import load_dotenv

class Page:
    def __init__(self, name, url):
        self.name = name
        self.url = url

load_dotenv()
location = "us-east4"

streamlit.set_page_config("Notebook RAG")

if "notebook" not in streamlit.session_state:
    with streamlit.spinner("Loading references..."):
        client = storage.Client()
        bucket = client.bucket(os.environ["BUCKET_NAME"])
        url_map = bucket.get_blob(os.environ["URL_MAP_NAME"])
        text = url_map.download_as_text()
        streamlit.session_state.notebook = [Page(name, url) for name, url in json.loads(text).items()]
        for page in streamlit.session_state.notebook:
            print(page.name)

def convert_title_to_notebook_link(title):
    print(f"Searching for '{title}'")
    page = next((page for page in streamlit.session_state.notebook if page.name == title), None)
    if page is None:
        for page in streamlit.session_state.notebook:
            if title.removesuffix("xa0") in page.name:
                return f"[{page.name}]({page.url})"
        return f"{title} *(URL not found)*"
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
            system_instruction="Keep responses brief. Use at most 4 sentences unless asked otherwise.",
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