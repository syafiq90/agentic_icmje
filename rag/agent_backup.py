# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import uuid
import vertexai
from google.adk.agents import Agent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext 
from google.adk.tools.retrieval.vertex_ai_rag_retrieval import VertexAiRagRetrieval
from vertexai.preview import rag
from openinference.instrumentation import using_session
from fpdf import FPDF  # Ensure 'fpdf' is in your requirements.txt
from google.genai import types  # <--- IMPORTANT IMPORT

# from rag.tracing import instrument_adk_with_arize

# _ = instrument_adk_with_arize()

from dotenv import load_dotenv
from .prompts import return_instructions_root

load_dotenv()

# Initialize Vertex AI
vertexai.init(
    project=os.environ.get("sea-gcp-apa-con-4a733-npd-1"), 
    location=os.environ.get("LOCATION", "asia-southeast1")
)


def search_icmje_policy(query: str) -> str:
    """
    Search the RAG corpus for specific ICMJE Recommendations, 
    ethics requirements, and manuscript reporting standards.
    """
    # Configure retrieval
    rag_retrieval_config = rag.RagRetrievalConfig(
        filter=rag.Filter(vector_distance_threshold=0.6),
        top_k=5,
    )
    
    # Query your corpus
    response = rag.retrieval_query(
        rag_resources=[
            rag.RagResource(rag_corpus=os.environ.get("RAG_CORPUS"))
        ],
        text=query,
        retrieval_config=rag_retrieval_config,
    )
    
    # Process results
    context = ""
    for context_chunk in response.contexts:
        context += f"\n[ICMJE SOURCE: {context_chunk.source_uri}]\n{context_chunk.text}\n"
    
    return context if context else "No specific ICMJE policy found in RAG."

# ask_vertex_retrieval = VertexAiRagRetrieval(
#     name='retrieve_rag_documentation',
#     description=(
#         'Use this tool to retrieve documentation and reference materials for the question from the RAG corpus.'
#     ),
#     rag_resources=[
#         rag.RagResource(
#             # please fill in your own rag corpus
#             # here is a sample rag corpus for testing purpose
#             # e.g. projects/123/locations/us-central1/ragCorpora/456
#             rag_corpus=os.environ.get("RAG_CORPUS")
#         )
#     ],
#     similarity_top_k=10,
#     vector_distance_threshold=0.6,
# )

# vision_agent = Agent(
#     model='gemini-2.0-flash-001',
#     name='vision_extractor',
#     instruction="""You are a query pre-processor. 
#     If the user provides an image or document, extract the core question or 
#     describe the visual content clearly in text. 
#     If it is just text, repeat the text exactly.
#     Your output must be text only"""
# )

# -------------------------------------------------------------------------
# TOOL 2: PDF GENERATION (Bypassing the Parser Error)
# -------------------------------------------------------------------------
async def generate_manuscript_pdf(content: str, **kwargs) -> str:
    """
    Generates a PDF from the reconstructed manuscript text and makes it 
    available for download.
    """
    # 1. Extract the context from kwargs (ADK injects it here automatically)
    # This prevents the 'Failed to parse parameter context' error.
    context = kwargs.get("context")

    # 2. Generate the PDF content
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    # Clean content for PDF encoding
    clean_content = content.encode('latin-1', 'ignore').decode('latin-1')
    pdf.multi_cell(0, 10, txt=clean_content)
    
    # Get PDF bytes
    pdf_bytes = pdf.output(dest='S')
    if isinstance(pdf_bytes, str):
        pdf_bytes = pdf_bytes.encode('latin-1')

    # 3. Save as an ADK Artifact so the download link works in the UI
    filename = f"manuscript_{uuid.uuid4().hex[:6]}.pdf"
    
    if context:
        artifact_part = types.Part.from_bytes(
            data=pdf_bytes,
            mime_type="application/pdf"
        )
        await context.save_artifact(filename=filename, artifact=artifact_part)
        return f"SUCCESS: The PDF has been generated. You can download '{filename}' from the Artifacts tab in the UI."
    
    # Fallback: Save locally if context is missing for some reason
    with open(filename, "wb") as f:
        f.write(pdf_bytes)
    return f"SUCCESS: PDF saved locally as {filename} (Context was unavailable)."

with using_session(session_id=uuid.uuid4()):
    root_agent = Agent(
        model='gemini-2.0-flash-001',
        name='medical_compliance_agent',
        instruction=return_instructions_root(),
        tools=[
            # ask_vertex_retrieval,
            search_icmje_policy,
            generate_manuscript_pdf
        ]
    )

# rag_agent = Agent(
#     model='gemini-2.0-flash-001',
#     name='rag_answerer',
#     instruction=return_instructions_root(),
#     tools=[
#         ask_vertex_retrieval,
#     ]
# )
#
# # 4. Root Agent using SequentialAgent
# with using_session(session_id=uuid.uuid4()):
#     root_agent = SequentialAgent(
#         name='multimodal_rag_flow',
#         sub_agents=[  # Correct parameter name
#             vision_agent,
#             rag_agent
#         ]
#     )
