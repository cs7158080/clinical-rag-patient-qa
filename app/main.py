try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

import os
import logging
from app.config import get_config
from app.logging_setup import setup_logging
from app.storage.db import init_db
from app.storage.pinecone_client import init_pinecone
from app.deidentification.reid_map import load as load_reid_map
from app.ui.gradio_app import build_app


def main():
    config = get_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    os.makedirs(config.data_dir, exist_ok=True)
    os.makedirs(config.logs_dir, exist_ok=True)

    db_path = os.path.join(config.data_dir, 'clinical_rag.db')
    reid_map_path = os.path.join(config.data_dir, 'reid_map.json')

    init_db(db_path)
    logger.info("Database initialized")

    try:
        pinecone_index = init_pinecone(config.pinecone_api_key, config.pinecone.index_name)
        logger.info("Pinecone connected")
    except Exception as e:
        logger.warning(f"Pinecone unavailable: {e}. Semantic search will be disabled.")
        pinecone_index = None

    reid_map = load_reid_map(reid_map_path)

    app = build_app(config, db_path, pinecone_index, reid_map_path)
    logger.info("Starting Gradio server at http://localhost:7860")
    app.launch(server_name="127.0.0.1", server_port=7860, share=False, allowed_paths=[config.patients_root])


if __name__ == "__main__":
    main()
