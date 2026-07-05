"""
ner.py — Hebrew NER singleton using ONNX runtime.

Intentionally does NOT import torch.  The model (avichr/heBERT_NER) must be
converted to ONNX format by setup.bat before this module can be used.

Public API
----------
load_ner_model(models_dir)   — load tokenizer + ONNX session (call once at startup)
is_model_loaded()            — True after a successful load_ner_model() call
extract_entities(text)       — run NER, return list of entity dicts
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_session = None       # onnxruntime.InferenceSession
_tokenizer = None     # transformers.AutoTokenizer
_label_map = None     # dict[int, str]  e.g. {0: "O", 1: "B-PER", ...}

# Default label map for avichr/heBERT_NER.
# Used as a fallback when config.json does not contain "id2label".
_DEFAULT_LABEL_MAP: "dict[int, str]" = {
    0: "O",
    1: "B-PER",
    2: "I-PER",
    3: "B-ORG",
    4: "I-ORG",
    5: "B-LOC",
    6: "I-LOC",
    7: "B-MISC",
    8: "I-MISC",
}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def load_ner_model(models_dir: str) -> None:
    """Load the heBERT_NER ONNX model.

    Expects the directory structure produced by setup.bat::

        <models_dir>/
        └── heBERT_NER_onnx/
            ├── model.onnx
            ├── config.json          (must contain "id2label")
            ├── tokenizer_config.json
            ├── vocab.txt
            └── ...

    Raises FileNotFoundError with an actionable message if the directory or
    model file is missing (i.e. setup.bat has not been run yet).

    This function is safe to call multiple times — subsequent calls are no-ops.
    """
    global _session, _tokenizer, _label_map

    if _session is not None:
        return  # already loaded

    onnx_path = os.path.join(models_dir, "heBERT_NER_onnx")

    if not os.path.isdir(onnx_path):
        raise FileNotFoundError(
            f"NER model directory not found: {onnx_path}\n"
            "Please run setup.bat to convert the heBERT_NER model to ONNX format."
        )

    model_file = os.path.join(onnx_path, "model.onnx")
    if not os.path.isfile(model_file):
        raise FileNotFoundError(
            f"ONNX model file not found: {model_file}\n"
            "Please run setup.bat to convert the heBERT_NER model to ONNX format."
        )

    # Load tokenizer (transformers — CPU only, no torch required for tokenisation)
    from transformers import AutoTokenizer  # noqa: PLC0415
    _tokenizer = AutoTokenizer.from_pretrained(onnx_path)

    # Load ONNX inference session
    import onnxruntime as ort  # noqa: PLC0415
    _session = ort.InferenceSession(model_file)

    # Load label map from config.json
    config_file = os.path.join(onnx_path, "config.json")
    if os.path.isfile(config_file):
        with open(config_file, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        raw_id2label = cfg.get("id2label", {})
        # config.json stores keys as strings; convert to int
        _label_map = {int(k): v for k, v in raw_id2label.items()}
    else:
        logger.warning(
            "config.json not found in %s — using default heBERT_NER label map.",
            onnx_path,
        )
        _label_map = dict(_DEFAULT_LABEL_MAP)

    logger.info("NER model loaded from %s", onnx_path)


def is_model_loaded() -> bool:
    """Return True if the NER model has been successfully loaded."""
    return _session is not None


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def extract_entities(text: str) -> "list[dict]":
    """Run Hebrew NER on *text* and return a list of entity dicts.

    Each dict has the keys::

        {
            "text":  str,          # the original entity text
            "label": str,          # "PER" | "ORG" | "LOC" | "MISC"
            "start": int,          # character offset in *text*
            "end":   int,          # character offset (exclusive)
        }

    Raises RuntimeError if the model has not been loaded.

    Implementation notes
    --------------------
    * Tokenisation uses return_offsets_mapping=True and return_tensors="np" so
      no torch tensors are created anywhere in this path.
    * The ONNX session returns logits; argmax gives token-level label ids.
    * BIO tagging is resolved to word-level spans: B-X starts a new entity of
      type X; I-X extends the current entity; any other tag closes the current
      entity.
    * Subword tokens (those whose offset_mapping start == previous token end
      and that are not the first token of a word) are merged into the same span.
    * [CLS] and [SEP] tokens (offset (0,0)) are skipped.
    """
    if not is_model_loaded():
        raise RuntimeError(
            "NER model is not loaded. Call load_ner_model(models_dir) first."
        )

    import numpy as np  # noqa: PLC0415 — available via onnxruntime dependency

    # Tokenise — use numpy tensors to avoid torch entirely
    inputs = _tokenizer(
        text,
        return_offsets_mapping=True,
        return_tensors="np",
        truncation=True,
        max_length=512,
    )

    offset_mapping = inputs.pop("offset_mapping")[0]  # shape (seq_len, 2)

    # Run ONNX inference — only input_ids and attention_mask are needed
    feed = {
        "input_ids": inputs["input_ids"],
        "attention_mask": inputs["attention_mask"],
    }
    # Some models also require token_type_ids
    if "token_type_ids" in [inp.name for inp in _session.get_inputs()]:
        feed["token_type_ids"] = inputs.get(
            "token_type_ids",
            np.zeros_like(inputs["input_ids"]),
        )

    outputs = _session.run(None, feed)
    logits = outputs[0][0]          # shape (seq_len, num_labels)
    label_ids = np.argmax(logits, axis=-1)  # shape (seq_len,)

    # Resolve token-level labels → entity spans
    entities: list[dict] = []
    current_entity: "dict | None" = None

    for idx, label_id in enumerate(label_ids):
        char_start, char_end = int(offset_mapping[idx][0]), int(offset_mapping[idx][1])

        # Skip special tokens ([CLS], [SEP], padding) which have offset (0, 0)
        if char_start == 0 and char_end == 0:
            if current_entity is not None:
                entities.append(current_entity)
                current_entity = None
            continue

        label_str: str = _label_map.get(int(label_id), "O")

        if label_str.startswith("B-"):
            # Close previous entity
            if current_entity is not None:
                entities.append(current_entity)
            entity_type = label_str[2:]  # strip "B-"
            current_entity = {
                "text": text[char_start:char_end],
                "label": entity_type,
                "start": char_start,
                "end": char_end,
            }

        elif label_str.startswith("I-"):
            entity_type = label_str[2:]
            if current_entity is not None and current_entity["label"] == entity_type:
                # Extend current entity (handles subword tokens and multi-token spans)
                current_entity["text"] = text[current_entity["start"]:char_end]
                current_entity["end"] = char_end
            else:
                # Orphan I- tag — treat as new entity
                if current_entity is not None:
                    entities.append(current_entity)
                current_entity = {
                    "text": text[char_start:char_end],
                    "label": entity_type,
                    "start": char_start,
                    "end": char_end,
                }

        else:
            # "O" or unknown — close any open entity
            if current_entity is not None:
                entities.append(current_entity)
                current_entity = None

    # Flush final entity
    if current_entity is not None:
        entities.append(current_entity)

    # Filter to only the entity types relevant to the de-id pipeline
    # (PER → person names; ORG → educational/other institutions)
    relevant = {"PER", "ORG"}
    return [e for e in entities if e["label"] in relevant]
