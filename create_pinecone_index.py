from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv
import os

from netfree_unstrict_ssl import unstrict_ssl
unstrict_ssl()

load_dotenv()

pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
index_name = os.getenv("PINECONE_INDEX_NAME")

if index_name not in [i["name"] for i in pc.list_indexes()]:
    pc.create_index(
        name=index_name,
        dimension=1024,  
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1")
    )

print(f"Index '{index_name}' ready")