import sys
import os
import logging


def init_db():
    from app.config import get_config
    from app.logging_setup import setup_logging
    from app.storage.db import init_db as db_init
    config = get_config()
    setup_logging(config)
    os.makedirs(config.data_dir, exist_ok=True)
    db_path = os.path.join(config.data_dir, 'clinical_rag.db')
    db_init(db_path)
    print(f"Database initialized at {db_path}")


def convert_ner():
    from app.config import get_config
    config = get_config()
    os.makedirs(config.models_dir, exist_ok=True)
    onnx_dir = os.path.join(config.models_dir, 'heBERT_NER_onnx')
    os.makedirs(onnx_dir, exist_ok=True)

    print("Downloading and converting heBERT_NER to ONNX format...")
    try:
        from huggingface_hub import snapshot_download
        from optimum.onnxruntime import ORTModelForTokenClassification
        from transformers import AutoTokenizer

        print("Downloading model files...")
        local_model_path = snapshot_download("avichr/heBERT_NER")
        print("Converting to ONNX...")
        model = ORTModelForTokenClassification.from_pretrained(local_model_path, export=True)
        tokenizer = AutoTokenizer.from_pretrained(local_model_path)
        model.save_pretrained(onnx_dir)
        tokenizer.save_pretrained(onnx_dir)
        print(f"NER model converted and saved to {onnx_dir}")
    except ImportError as e:
        print(f"ImportError: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR during NER conversion: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m app.setup [db|ner]")
        sys.exit(1)

    command = sys.argv[1]
    if command == 'db':
        init_db()
    elif command == 'ner':
        convert_ner()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
