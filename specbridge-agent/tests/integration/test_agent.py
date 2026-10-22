# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from google.adk.workflow import Workflow

from app.agent import root_agent


def test_agent_graph_loads_with_bounded_parallel_reviews() -> None:
    assert isinstance(root_agent, Workflow)
    assert root_agent.name == "specbridge_agent"
    names = {node.name for node in root_agent.graph.nodes}
    assert "join_initial_reviews" in names
    assert "join_rebuttals" in names
    assert sum(name.startswith("review1_") for name in names) == 5
    assert sum(name.startswith("review2_") for name in names) == 5


def test_structured_llm_nodes_retry_validation_once() -> None:
    llm_nodes = [
        node
        for node in root_agent.graph.nodes
        if node.name
        in {
            "extractor",
            "trace_linker",
            "moderator",
            "conflict_detector",
            "document_writer",
        }
        or node.name.startswith(("review1_", "review2_"))
    ]

    assert len(llm_nodes) == 15
    assert all(node.retry_config is not None for node in llm_nodes)
    assert all(node.retry_config.max_attempts == 2 for node in llm_nodes)
    assert all(
        node.retry_config.exceptions == ["ValidationError"] for node in llm_nodes
    )
