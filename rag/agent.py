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
from google.adk.agents import Agent
from google.adk.tools import ToolContext
from vertexai.preview import rag
from vertexai.generative_models import GenerativeModel, Part  # For visual processing
from openinference.instrumentation import using_session
from google.genai import types 
from google.genai import Client
from dotenv import load_dotenv
from .prompts import return_instructions_root
import re
import re
import fitz
from fpdf import FPDF

 # PyMuPDF

load_dotenv()
genai_client = Client()

# Initialize Vertex AI
vertexai.init(
    project=os.environ.get("dcsea-tiktok-poc"), 
    location="us-central1" # Ubah ke us-central1
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

import logging

# Setup logging sederhana agar kita bisa lihat error di terminal
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Konfigurasi Folder
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(BASE_DIR, "inputs")
IMAGE_DIR = os.path.join(BASE_DIR, "temp_figures")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")

for d in [INPUT_DIR, IMAGE_DIR, OUTPUT_DIR]:
    os.makedirs(d, exist_ok=True)



# # --- TOOL 1: JEMBATAN UI KE LOKAL ---
async def save_ui_file_to_local(filename: str, tool_context: ToolContext):
    """
    Saves the file attached in the UI to the local 'inputs' folder. 
    Run this tool first before processing any PDF.

    """
    try:
        # 1. Ambil user_content dari tool_context
        user_content = tool_context.user_content
        if not user_content or not user_content.parts:
            return "Error: Tidak ada konten yang ditemukan dalam pesan user."

        found_part = None
        
        # 2. Iterasi parts untuk mencari inline_data yang sesuai dengan filename
        for part in user_content.parts:
            # Cek apakah part ini mengandung inline_data (file)
            if hasattr(part, 'inline_data') and part.inline_data:
                # Cek apakah nama filenya cocok
                # Catatan: Kadang filename dari LLM berbeda tipis, kita cek display_name
                if part.inline_data.display_name == filename or filename in part.inline_data.display_name:
                    found_part = part.inline_data
                    break
        
        # 3. Jika tidak ketemu secara spesifik, ambil file pertama yang ada (fallback)
        if not found_part:
            for part in user_content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    found_part = part.inline_data
                    filename = found_part.display_name # Update nama file ke nama asli
                    break

        if found_part:
            # 4. Buat folder dan simpan datanya (bytes)
            os.makedirs(INPUT_DIR, exist_ok=True)
            path = os.path.join(INPUT_DIR, filename)
            
            with open(path, "wb") as f:
                f.write(found_part.data) # .data berisi bytes PDF
                
            return f"SUCCESS: File '{filename}' berhasil disimpan secara lokal di {path}"
        
        return f"ERROR: File '{filename}' tidak ditemukan di dalam pesan."

    except Exception as e:
        return f"Gagal menyimpan file: {str(e)}"
    

vision_model = GenerativeModel("gemini-2.0-flash-001")

async def classify_image_with_vision(image_bytes: bytes) -> bool:
    image_part = types.Part.from_bytes(
        data=image_bytes,
        mime_type="image/png"
    )

    response = genai_client.models.generate_content(
        model="gemini-2.0-flash-001",
        contents=[
            "Classify this image as SCIENTIFIC_FIGURE or PUBLISHER_ARTIFACT. "
            "Respond with ONLY ONE WORD.",
            image_part
        ],
        config=types.GenerateContentConfig(
            temperature=0
        )
    )

    result = response.text.strip().upper()
    print(f"Vision result: {result}")

    return result == "SCIENTIFIC_FIGURE"

def normalize_figures(content: str) -> str:
    """
    Prevent duplicate figure captions by ensuring
    each [[INSERT_IMAGE]] is preceded by exactly one caption.
    """
    lines = content.splitlines()
    seen = set()
    output = []

    for line in lines:
        if line.strip().startswith("Figure ") and line in seen:
            continue
        if line.strip().startswith("Figure "):
            seen.add(line)
        output.append(line)

    return "\n".join(output)

# --- TOOL 2: EKSTRAKSI DARI LOKAL (TIDAK BUTUH CONTEXT) ---
async def extract_images_from_local(filename: str):
    """
    Extracts images from a PDF that has been synchronized to local storage.
    Args:
        filename: Name of the PDF file in the 'inputs' folder.
    """
    print(f"filename: {filename}")

    file_path = os.path.join(INPUT_DIR, "JCRMHS.pdf")
    if not os.path.exists(file_path):
        return f"Error: File belum disinkronkan. Jalankan 'save_ui_file_to_local' dulu."

    try:
        doc = fitz.open(file_path)
        image_count = 0
        # Bersihkan folder gambar lama
        for f in os.listdir(IMAGE_DIR): os.remove(os.path.join(IMAGE_DIR, f))

        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                base_image = doc.extract_image(xref)
                if not await classify_image_with_vision(base_image["image"]):
                    continue    
                image_count += 1
                path = os.path.join(IMAGE_DIR, f"figure{image_count}.png")
                with open(path, "wb") as f:
                    f.write(base_image["image"])
        doc.close()
        return f"SUCCESS: {image_count} gambar diekstrak secara lokal."
    except Exception as e:
        return f"Error ekstraksi: {str(e)}"

def has_manual_images(tool_context: ToolContext) -> bool:
    user_content = tool_context.user_content
    if not user_content or not user_content.parts:
        return False

    for part in user_content.parts:
        if hasattr(part, "inline_data") and part.inline_data:
            if part.inline_data.mime_type.startswith("image/"):
                return True
    return False


async def bootstrap_inputs(tool_context: ToolContext):
    """
    Persist inputs ONLY when needed.
    Safe for:
    - PDF only
    - Text only
    - Text + images
    """
    if has_manual_images(tool_context):
        await save_attached_images_to_local(tool_context)

async def save_attached_images_to_local(tool_context):
    """
    Saves manually attached images (non-PDF) to temp_figures folder.
    """
    user_content = tool_context.user_content
    if not user_content or not user_content.parts:
        return "No attached images found."
    print(f"User content parts: {len(user_content.parts)}")
    image_count = 0
    for part in user_content.parts:
        if hasattr(part, "inline_data") and part.inline_data:
            if part.inline_data.mime_type.startswith("image/"):
                image_count += 1
                filename = f"figure{image_count}.png"
                path = os.path.join(IMAGE_DIR, filename)

                with open(path, "wb") as f:
                    f.write(part.inline_data.data)

    return f"SUCCESS: {image_count} manual images saved."

def inject_manual_images(content: str):
    """
    Insert image tags ONLY after existing Figure X captions.
    Do NOT duplicate Figure titles or captions.
    """
    for i, img in enumerate(sorted(os.listdir(IMAGE_DIR)), start=1):
        tag = f"[[INSERT_IMAGE: {img}]]"

        # Regex: find "Figure X:" block (caption already exists)
        pattern = rf"(Figure {i}:[^\n]+(?:\n(?!Figure \d:).+)*)"

        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            block = match.group(1)
            if tag not in block:
                content = content.replace(block, f"{block}\n\n{tag}", 1)
        else:
            # fallback ONLY if figure truly not referenced
            content += f"\n\n{tag}\n"

    return content

def sanitize_text_for_pdf(content: str) -> str:
    """
        1. Remove invisible / PDF-breaking unicode
        2. Force image tags to be isolated by newlines
        3. Normalize whitespace
    """

    # Remove known PDF-breaking chars
    bad_chars = [
        "\u000c",  # form feed
        "\u00ad",  # soft hyphen
        "\u2028",  # line separator
        "\u2029",  # paragraph separator
    ]
    for ch in bad_chars:
        content = content.replace(ch, "")

    # Force image tags to be on their own lines
    content = re.sub(
        r"\s*\[\[INSERT_IMAGE:\s*(.*?)\]\]\s*",
        r"\n\n[[INSERT_IMAGE: \1]]\n\n",
        content
    )

    # Normalize newlines
    content = re.sub(r"\n{3,}", "\n\n", content)

    return content.strip()

# --- TOOL 3: GENERATE PDF KE LOKAL ---
def generate_reconstructed_pdf_local(content: str):
    """
    Generates the final PDF locally in the 'outputs' folder.
    """
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)

        parts = re.split(r'(\[\[INSERT_IMAGE:.*?\]\])', content)
        usable_width = pdf.w - pdf.l_margin - pdf.r_margin

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if part.startswith("[[INSERT_IMAGE:"):
                img_name = part.replace("[[INSERT_IMAGE:", "").replace("]]", "").strip()
                img_path = os.path.join(IMAGE_DIR, img_name)

                if os.path.exists(img_path):
                    pdf.ln(5)
                    pdf.set_x(pdf.l_margin)
                    pdf.image(img_path, w=usable_width)
                    pdf.ln(10)
                    pdf.set_x(pdf.l_margin)

            else:
                clean_text = sanitize_text_for_pdf(part)
                clean_text = clean_text.encode('latin-1', 'replace').decode('latin-1')
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(usable_width, 7, clean_text)

        save_path = os.path.join(OUTPUT_DIR, f"reconstructed_{uuid.uuid4().hex[:4]}.pdf")
        pdf.output(save_path)
        return f"SUCCESS: PDF tersimpan secara lokal di: {save_path}"
    except Exception as e:
        return f"Error PDF: {str(e)}"
    
def is_pdf_uploaded(tool_context: ToolContext) -> bool:
    user_content = tool_context.user_content
    if not user_content or not user_content.parts:
        return False

    for part in user_content.parts:
        if hasattr(part, "inline_data") and part.inline_data:
            if part.inline_data.mime_type == "application/pdf":
                return True
    return False

async def reconstruct_and_generate_pdf(content: str, tool_context: ToolContext):
    """
    Phase 2 orchestrator:
    - Decide mode (PDF vs MANUAL)
    - Inject images if MANUAL
    - Generate final PDF
    """
    await bootstrap_inputs(tool_context)

    if is_pdf_uploaded(tool_context):
        mode = "PDF"
    else:
        mode = "MANUAL"

    if mode == "MANUAL":
        content = inject_manual_images(content)

    return generate_reconstructed_pdf_local(content)

root_agent = Agent(
    model='gemini-2.0-flash-001',
    name='medical_compliance_agent',
    instruction=return_instructions_root(),
    tools=[
        # ask_vertex_retrieval,
        search_icmje_policy,
        reconstruct_and_generate_pdf,
        extract_images_from_local,
        save_ui_file_to_local,
        save_attached_images_to_local
    ]
)

