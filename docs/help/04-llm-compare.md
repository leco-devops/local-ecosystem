# Ollama vs AirLLM

| | **Ollama** | **AirLLM** |
|---|------------|------------|
| **Format** | GGUF (Ollama registry) | HuggingFace safetensors |
| **Sweet spot** | Small/medium models, fast iteration | 32B–405B on limited RAM |
| **Container** | `ollama` | `airllm` |
| **URL** | `ollama.lh` | `airllm.lh` |
| **Model id example** | `llama3.2:3b` | `Qwen/Qwen2.5-7B-Instruct` |
| **GPU on macOS Docker** | Metal via Ollama's build | CPU only (no Apple GPU in Linux VM) |
| **Dashboard section** | Infrastructure **5 · Ollama** | Infrastructure **6 · AirLLM** |

They do **not** share model files. Pulling `llama3` in Ollama does not appear in AirLLM.

## Which should I use?

- **Daily dev, chat, code assist under ~15 GB disk** → Ollama.
- **Experiment with 70B+ without buying a GPU rig** → AirLLM (expect slow CPU inference and large downloads).

You can run **both** simultaneously; each keeps at most one model hot in RAM by default.
