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

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 7860

logger = logging.getLogger(__name__)


def create_app(config):
    """Initialise storage and build the Gradio app (shared by main() and app.desktop).

    Returns (app, pinecone_ok). pinecone_ok is False when Pinecone could not be
    reached — the app still works, but semantic search is disabled.
    """
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
    return app, pinecone_index is not None


def launch_kwargs(config) -> dict:
    """Server settings shared by the console and desktop launchers."""
    return dict(
        server_name=SERVER_HOST,
        server_port=SERVER_PORT,
        share=False,
        allowed_paths=config.patients_roots,
    )


def main():
    config = get_config()
    setup_logging(config)
    app, _ = create_app(config)
    logger.info(f"Starting Gradio server at http://{SERVER_HOST}:{SERVER_PORT}")
    app.launch(**launch_kwargs(config))


if __name__ == "__main__":
    main()
