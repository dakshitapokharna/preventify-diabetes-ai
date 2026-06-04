"""
One-time script: export reranker model to ONNX, then INT8-quantize it.

Run once before starting the server:
    python scripts/quantize_reranker.py

Output: D:/hf_cache/reranker_onnx_int8/
  - model_quantized.onnx
  - tokenizer files (copied from HF)

Requires: pip install "optimum[onnxruntime]" onnxruntime
"""

from pathlib import Path
from optimum.onnxruntime import ORTModelForSequenceClassification, ORTQuantizer
from optimum.onnxruntime.configuration import AutoQuantizationConfig

MODEL_ID = "BAAI/bge-reranker-v2-m3"
ONNX_DIR = Path("D:/hf_cache/reranker_onnx")
INT8_DIR = Path("D:/hf_cache/reranker_onnx_int8")


def main():
    print(f"[1/2] Exporting {MODEL_ID} -> ONNX ({ONNX_DIR}) ...")
    model = ORTModelForSequenceClassification.from_pretrained(MODEL_ID, export=True)
    model.save_pretrained(ONNX_DIR)
    print("      Export done.")

    print(f"[2/2] Quantizing to INT8 ({INT8_DIR}) ...")
    quantizer = ORTQuantizer.from_pretrained(ONNX_DIR)
    qconfig = AutoQuantizationConfig.avx2(is_static=False, per_channel=False)
    quantizer.quantize(save_dir=INT8_DIR, quantization_config=qconfig)
    print(f"      Done -> {INT8_DIR}")
    print("      Start your server now - load_reranker() will auto-detect this path.")


if __name__ == "__main__":
    main()
