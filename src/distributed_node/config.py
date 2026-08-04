"""Carga y validación de la configuración de Sonantia Network 1.0."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .message_catalog import CompiledMessageCatalog, compile_message_catalog
from .models import MessageCatalog, NodeConfig, OperatorMessage, SonantiaNetworkConfig


def load_json(path: Path) -> object:
    """Carga un archivo JSON usando UTF-8."""
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_model[ModelT: BaseModel](path: Path, model_type: type[ModelT]) -> ModelT:
    """Carga un JSON y lo valida con el modelo indicado."""
    return model_type.model_validate(load_json(path))


def load_node_configuration(config_dir: Path) -> NodeConfig:
    """Carga la identidad y ubicación del nodo desde la fuente canónica."""
    return load_model(config_dir / "node.json", NodeConfig)


def load_network_configuration(config_dir: Path) -> SonantiaNetworkConfig:
    """Carga topología, época y límites operativos de Sonantia."""
    return load_model(config_dir / "sonantia-network.json", SonantiaNetworkConfig)


def load_configuration(
    config_dir: Path,
) -> tuple[NodeConfig, SonantiaNetworkConfig, CompiledMessageCatalog, OperatorMessage]:
    """Carga los documentos necesarios para ejecutar un nodo Sonantia."""
    node, network, operator_message = load_core_configuration(config_dir)
    catalog = load_message_catalog(config_dir)
    return node, network, catalog, operator_message


def load_core_configuration(
    config_dir: Path,
) -> tuple[NodeConfig, SonantiaNetworkConfig, OperatorMessage]:
    return (
        load_node_configuration(config_dir),
        load_network_configuration(config_dir),
        load_model(config_dir / "operator-message.json", OperatorMessage),
    )


def load_message_catalog(config_dir: Path) -> CompiledMessageCatalog:
    definition = load_model(config_dir / "message-catalog.json", MessageCatalog)
    return compile_message_catalog(definition, config_dir)
