# EchoMemory-MM 30-Paper Map and Nano Benchmark

Date: 2026-06-13

## Why this note exists

The thread objective expanded from "10 recent papers" to **30 recent papers**.
This note does two things:

1. upgrades the literature map from 10 to 30 recent primary sources
2. records a stronger nano benchmark for the dual-backbone idea

Important correction:

- several older local notes used guessed or stale paper links
- this note keeps only a **validated set** of references that were rechecked from current local evidence and recent primary-source lookups

---

## 1. What changed in the nano evidence

New experiment:

- script:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_benchmark.py`
- results json:
  - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_benchmark_results.json`
- html:
  - `/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_dual_backbone_benchmark_20260613.html`

Current controlled toy benchmark result:

- cases: 12
- tree-only: 3 / 12
- graph-only: 5 / 12
- dual-backbone: 8 / 12

Per-family:

- temporal: tree 3 / 3, graph 0 / 3, dual 3 / 3
- relational: tree 0 / 3, graph 1 / 3, dual 1 / 3
- temporal-relational: tree 0 / 3, graph 1 / 3, dual 1 / 3
- visual: tree 0 / 3, graph 3 / 3, dual 3 / 3

Interpretation:

- the benchmark still does **not** prove production readiness
- it does give stronger mechanistic evidence that:
  - temporal tree solves chronology navigation
  - graph solves relation / image grounding
  - planner-routed dual-backbone is more stable than a single backbone

---

## 2. Validated 30-paper map

The list below is intentionally mixed:

- benchmarks
- retrieval papers
- graph papers
- systems papers
- agent-memory lifecycle papers

Not every item is a "top conference full paper".
The honest description is:

> a 30-paper recent reading map built from top-conference papers, benchmark papers,
> and high-impact primary sources centered on 2024-2026, with a very small number
> of explicitly marked foundational carry-over references that remain directly useful
> for EchoMemory.

Scope note:

- the map is intentionally centered on **2024-2026**
- one important exception is **Self-RAG (2023)**, which we keep because it is still
  one of the clearest primary references for retrieval self-reflection and on-demand
  retrieval control

### A. Benchmark and evaluation pressure

1. LoCoMo  
   Link: https://arxiv.org/abs/2402.17753  
   Use for EchoMemory: temporal / relation / multi-hop conversational pressure

2. LongMemEval  
   Link: https://arxiv.org/abs/2410.10813  
   Use for EchoMemory: memory indexing / retrieval / reading separation

3. LongMemEval-V2  
   Link: https://arxiv.org/abs/2605.12493  
   Use for EchoMemory: stronger agent-memory framing toward experienced-colleague style tasks

4. Regimes: An Auditable, Held-Out-Gated Improvement Loop Demonstrated on LongMemEval with ActiveGraph  
   Link: https://arxiv.org/abs/2606.10241  
   Use for EchoMemory: auditable improvement loop and evaluation gating

5. When Stored Evidence Stops Being Usable: Scale-Conditioned Evaluation of Agent Memory  
   Link: https://arxiv.org/abs/2605.07313  
   Use for EchoMemory: evaluate retrieval usability, not just storage completeness

6. WhenLoss: Diagnosing Write and Retrieval Bottlenecks in Long-Context Memory Systems  
   Link: https://arxiv.org/abs/2605.24579  
   Use for EchoMemory: separate write-path and retrieval-path failures

### B. Hierarchical and temporal retrieval

7. RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval  
   Link: https://arxiv.org/abs/2401.18059  
   Use for EchoMemory: temporal tree / abstraction hierarchy

8. MemoRAG: Boosting Long Context Processing with Global Memory-Enhanced Retrieval Augmentation  
   Link: https://arxiv.org/abs/2409.05591  
   Use for EchoMemory: coarse-to-fine memory-guided retrieval

9. GraphReader: Building Graph-based Agent to Enhance Long-Context Abilities of Large Language Models  
   Link: https://arxiv.org/abs/2406.14550  
   Use for EchoMemory: staged graph exploration instead of one-shot context stuffing

10. ByteRover: Agent-Native Memory Through LLM-Curated Hierarchical Context  
    Link: https://arxiv.org/abs/2604.01599  
    Use for EchoMemory: hierarchy as first-class memory substrate

11. TiMem: Temporal-Hierarchical Memory Consolidation for Long-Horizon Conversational Agents  
    Link: https://arxiv.org/abs/2601.02845  
    Use for EchoMemory: temporal hierarchy as consolidation structure

12. Hierarchical Memory for High-Efficiency Long-Term Reasoning in LLM Agents  
    Link: https://arxiv.org/abs/2507.22925  
    Use for EchoMemory: coarse-to-fine route planning

### C. Graph and structured recall

13. HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models  
    Link: https://arxiv.org/abs/2405.14831  
    Use for EchoMemory: graph as main recall backbone

14. From RAG to Memory: Non-Parametric Continual Learning for Large Language Models  
    Link: https://arxiv.org/abs/2502.14802  
    Use for EchoMemory: continual-memory perspective beyond static retrieval

15. Zep: A Temporal Knowledge Graph Architecture for Agent Memory  
    Link: https://arxiv.org/abs/2501.13956  
    Use for EchoMemory: temporal KG and memory evolution

16. LEGO-GraphRAG  
    Link: https://arxiv.org/abs/2411.05844  
    Use for EchoMemory: modular graph retrieval pipeline

17. H-Mem: A Novel Memory Mechanism for Evolving and Retrieving Agent Memory via a Hybrid Structure  
    Link: https://arxiv.org/abs/2605.15701  
    Use for EchoMemory: explicit hybrid tree + graph reasoning

18. APEX-MEM: Agentic Semi-Structured Memory with Temporal Reasoning for Long-Term Conversational AI  
    Link: https://arxiv.org/abs/2604.14362  
    Use for EchoMemory: semi-structured memory plus temporal reasoning

### D. Memory lifecycle and systems

19. Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory  
    Link: https://arxiv.org/abs/2504.19413  
    Use for EchoMemory: extraction / consolidation / retrieval balance

20. LightMem: Lightweight and Efficient Memory-Augmented Generation  
    Link: https://arxiv.org/abs/2510.18866  
    Use for EchoMemory: online-light / offline-heavy consolidation

21. MemOS: An Operating System for Memory-Augmented Generation (MAG) in Large Language Models  
    Link: https://arxiv.org/abs/2505.22101  
    Use for EchoMemory: memory as a governed system resource

22. Infini Memory: Maintainable Topic Documents for Long-Term LLM Agent Memory  
    Link: https://arxiv.org/abs/2606.10677  
    Use for EchoMemory: maintainable topic dossiers above atoms

23. AgentIR: A Workload-Adaptive Cascade Retrieval Substrate for Long-Term Conversational Memory  
    Link: https://arxiv.org/abs/2605.25092  
    Use for EchoMemory: adaptive retrieval cascades by workload

24. ConvMemory: A Lightweight Learned Memory Reranker, a Negative Attribution Result, and a Research-Preview Conflict Editor  
    Link: https://arxiv.org/abs/2605.28062  
    Use for EchoMemory: learned reranking and conflict editing

### E. Agentic control, policy, and multimodal direction

25. MIRIX: Multi-Agent Memory System for LLM-Based Agents  
    Link: https://arxiv.org/abs/2507.07957  
    Use for EchoMemory: typed memory planes and multimodal direction

26. Mem-T: Densifying Rewards for Long-Horizon Memory Agents  
    Link: https://arxiv.org/abs/2601.23014  
    Use for EchoMemory: learned memory policy and action logging

27. E-mem: Multi-agent based Episodic Context Reconstruction for LLM Agent Memory  
    Link: https://arxiv.org/abs/2601.21714  
    Use for EchoMemory: episodic reconstruction beyond flat retrieval

28. D-MEM: Dopamine-Gated Agentic Memory via Reward Prediction Error Routing  
    Link: https://arxiv.org/abs/2603.14597  
    Use for EchoMemory: reward-routed memory policy

29. Field-Theoretic Memory for AI Agents: Continuous Dynamics for Context Preservation  
    Link: https://arxiv.org/abs/2602.21220  
    Use for EchoMemory: non-discrete memory dynamics as a contrastive design line

30. Self-RAG *(foundational carry-over, 2023)*  
   Link: https://openreview.net/forum?id=hSyW5go0v8  
   Use for EchoMemory: retrieval self-reflection and second-pass correction

---

## 3. What this 30-paper map says EchoMemory should do next

The main conclusion did not become weaker after expanding from 10 to 30 papers.
It became clearer:

1. EchoMemory should move toward **planner-routed dual-backbone memory**
   - temporal tree for chronology
   - graph for relation / event / image evidence

2. EchoMemory should add a formal **readiness plane**
   - messages_persisted
   - atoms_ready
   - graph_ready
   - tree_ready
   - qa_ready

3. EchoMemory should separate:
   - hot-path ingestion
   - cold-path consolidation
   - retrieval-time evidence composition

4. EchoMemory should treat **image evidence** as first-class memory,
   not as an afterthought attached to text memory.

5. EchoMemory should evaluate not only accuracy, but also:
   - readiness correctness
   - temporal fidelity
   - relation path quality
   - evidence usability

---

## 4. What to write into the paper next

The current strongest paper direction is still:

**EchoMemory-MM: Dual-Backbone Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

The next writing upgrade should add:

1. a 30-paper related-work map grouped by function
2. a clearer statement that some local earlier citations were corrected
3. the new 12-case toy benchmark
4. a stronger claim boundary:
   - toy benchmark supports mechanism
   - benchmark-scale LoCoMo / LongMemEval is still future work
