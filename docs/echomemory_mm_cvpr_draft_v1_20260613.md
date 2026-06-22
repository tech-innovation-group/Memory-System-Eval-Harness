# EchoMemory-MM Draft v1

Date: 2026-06-13

## Title

**EchoMemory-MM: Multimodal Temporal Graph Memory for Long-Horizon Personal Agents**

## Honest positioning

This is a **prospective CVPR branch**, not yet a complete submission-ready paper.

What is already true:

1. The repository now contains a concrete multimodal nano prototype:
   - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_temporal_graph.py`
2. The repository also contains a toy multimodal ablation:
   - `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_ablation_experiment.py`
3. The toy multimodal ablation already demonstrates a real systems point:
   - for OCR-only visual answers, text-only memory cannot recover the answer
   - multimodal retrieval can surface `image_evidence` as the top path

What is **not yet true**:

1. EchoMemory real code does not yet contain a full multimodal ingest path
2. The planner is not yet a real multimodal retrieval controller in production code
3. There is not yet a real multimodal benchmark run on the main system
4. Therefore this branch should be presented as a **designed and partially prototyped extension**, not as a finished real-system result

## Abstract

Long-horizon personal agents increasingly operate over heterogeneous evidence streams that include text, screenshots, interface captures, and document snippets. Existing long-term memory systems for agents remain largely text-first: they may preserve conversational facts and temporal summaries, but they do not treat visual evidence as a first-class memory object. This creates a fundamental recall gap for queries whose answers are only visible in screenshots, OCR-bearing images, or layout-grounded document regions. We propose **EchoMemory-MM**, a multimodal temporal graph memory architecture that extends a text-first stream-to-structure memory system with image-evidence nodes, multimodal grounding edges, and planner-guided retrieval over text and visual memory planes. EchoMemory-MM preserves the incremental structure of session streams while allowing screenshots and other visual observations to enter the same temporal memory graph as fact, event, and entity nodes. We instantiate the design as a multimodal nano prototype and toy ablation in the EchoMemory research stack. The current prototype demonstrates two core claims: visual queries should prioritize image-evidence nodes rather than text-only summaries, and OCR-only answers cannot be reliably recovered from text memory alone. These results motivate a full multimodal memory system in which visual grounding is part of long-term memory construction rather than an external attachment.

## 1. Motivation

The current EchoMemory-TG line solves a real text-side systems problem:

- append-only session streams
- incremental atom extraction
- temporal and relational retrieval
- readiness-aware memory visibility

But that line remains text-first.

For a CVPR-style contribution, the key missing step is:

> visual evidence must become a memory object, not only an attachment.

This matters because some personal-agent questions are intrinsically visual:

- “What time was visible in the screenshot?”
- “Which city name appeared on the station board?”
- “What layout or object arrangement did the user save as inspiration?”
- “Which version of the slide or diagram did the user point to?”

These are not merely text questions with images nearby. The answer may exist only in OCR, regions, or visual layout.

## 2. Core idea

EchoMemory-MM extends the text-first memory planes with a visual evidence plane.

### 2.1 Memory planes

1. **Session plane**
   - append-only stream of text turns and multimodal observations

2. **Atomic text plane**
   - text-derived facts, events, relations

3. **Visual evidence plane**
   - screenshot/image/document-region evidence nodes
   - OCR, caption, tags, region metadata, visual timestamp

4. **Temporal graph plane**
   - fact nodes
   - event nodes
   - entity nodes
   - image evidence nodes
   - optional region nodes

5. **Structured plane**
   - organized text summaries
   - multimodal entity dossiers
   - event summaries that cite both textual and visual evidence

### 2.2 New node types

In addition to:

- `fact:{atom_id}`
- `event:{atom_id}`
- `entity:{name}`

EchoMemory-MM adds:

- `image_evidence:{obs_id}`
- optionally `region:{obs_id}:{region_id}`

### 2.3 New edges

Examples:

- `image_evidence -> visual_evidence_of -> fact`
- `image_evidence -> supports_event -> event`
- `image_evidence -> shows -> entity`
- `region -> grounded_in -> image_evidence`
- `region -> mentions_text -> OCR span`

## 3. Planner extension

The central retrieval change is not “search images too”.

The real change is:

> the planner must know when a query is visual-first.

Suggested planner intents:

- `visual`
- `visual_temporal`
- `visual_relational`
- `text_temporal`
- `text_relational`
- `general`

Routing examples:

- visual query -> `image_evidence` first
- OCR/time query -> `image_evidence` then `event`
- style/layout query -> `image_evidence + fact`
- textual event query -> `event + fact`, with image evidence as support

## 4. What is already implemented in prototype form

### 4.1 Multimodal nano

File:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_temporal_graph.py`

It already includes:

- text observations
- image observations with `caption`, `ocr`, `tags`
- `image_evidence` nodes
- `visual_evidence_of` and `supports_event` edges
- a visual query planner

### 4.2 Toy multimodal ablation

Files:

- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_ablation_experiment.py`
- `/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_ablation_results.json`

Current toy result summary:

- `text_only_correct = 3`
- `multimodal_correct = 3`
- `visual_only_gain_cases = 2`

Interpretation:

- multimodal does not need to dominate text-only on every question
- the real win is on questions where the answer is only visible in image evidence

## 5. Why this branch is CVPR-relevant

The CVPR-relevant claim is not:

- “we also accept images”

The stronger claim is:

1. visual evidence is projected into long-term memory structure
2. visual evidence is temporally grounded
3. multimodal retrieval is planner-guided rather than naive concatenation
4. long-horizon agent memory can unify text facts and image-grounded evidence in one temporal graph

That is a more natural vision/multimodal story than the current text-first TG paper.

## 6. What is still missing for a real CVPR submission

### 6.1 Real multimodal ingest in main code

Need to add:

- screenshot / image observation API
- OCR/caption embedding and storage
- image evidence node construction in the real graph path

### 6.2 Real multimodal benchmark

Need at least one of:

- a visual-memory QA benchmark
- a controlled screenshot-memory task
- a document-memory benchmark where layout/region evidence matters

### 6.3 Real multimodal planner and retrieval logs

Need:

- planner traces showing visual-first routing
- evidence logs showing image node selection
- error analysis separating OCR miss vs planner miss vs graph miss

### 6.4 Real ablations

Suggested table:

1. text-only
2. text + image features without graph grounding
3. text + image evidence nodes
4. text + image evidence nodes + multimodal planner

## 7. Minimal implementation path

If we continue this branch, the practical order should be:

1. add image observation ingest to real session storage
2. add image evidence nodes to real graph builder
3. add multimodal planner intent in real search path
4. add one controlled multimodal memory task
5. run real ablations

## 8. Relationship to EchoMemory-TG

The relationship between the two papers should be:

- **EchoMemory-TG**: text-first stream-to-structure memory architecture
- **EchoMemory-MM**: multimodal extension where visual evidence becomes a first-class long-term memory object

This is cleaner than forcing the current text-first system directly into a CVPR frame.

## 9. Honest limitations

At the current repository state:

1. this multimodal branch is still a prototype line
2. the strongest current evidence is toy, not full-system benchmark evidence
3. the main contribution remains architectural and systems-oriented
4. a real CVPR version still requires substantial implementation and evaluation work

## 10. Immediate next experiment

The smallest non-toy next step would be:

1. ingest a handful of screenshot observations into a real EchoMemory workspace
2. create image evidence nodes in the real graph path
3. run a tiny visual-memory retrieval task with explicit planner traces

That would turn the current CVPR branch from “promising prototype” into “early real-system multimodal memory result”.
