import warnings
from abc import ABC
from dataclasses import dataclass
from pathlib import Path
from typing import Union, Type, Dict, Any, List, Generator, Tuple

import numpy as np
import tensorflow as tf
import torch

from nebullvm.config import TVM_FILENAMES
from nebullvm.inference_learners.base import (
    BaseInferenceLearner,
    LearnerMetadata,
    PytorchBaseInferenceLearner,
    TensorflowBaseInferenceLearner,
)
from nebullvm.base import ModelParams, DeepLearningFramework


try:
    import tvm
    from tvm.contrib.graph_executor import GraphModule
    from tvm.runtime import Module
except ImportError:
    warnings.warn("Not found any valid tvm installation")
    Module = object
    GraphModule = object


@dataclass
class ApacheTVMInferenceLearner(BaseInferenceLearner, ABC):
    graph_executor_module: GraphModule
    input_names: List[str]
    lib: Module
    target: str

    def _predict_array(
        self, input_arrays: Generator[np.ndarray, None, None]
    ) -> Generator[np.ndarray, None, None]:
        for name, array in zip(self.input_names, input_arrays):
            self.graph_executor_module.set_input(name, array)
        self.graph_executor_module.run()

        tvm_outputs = (
            self.graph_executor_module.get_output(
                i,
                tvm.nd.empty(
                    (
                        self.network_parameters.batch_size,
                        *output_size,
                    )
                ),
            ).numpy()
            for i, output_size in enumerate(
                self.network_parameters.output_sizes
            )
        )
        return tvm_outputs

    def save(self, path: Union[str, Path], **kwargs):
        path = Path(path)
        metadata = LearnerMetadata.from_model(
            self, input_names=self.input_names, target=self.target, **kwargs
        )
        metadata.save(path)
        self.lib.export_library(path / TVM_FILENAMES["engine"])

    @classmethod
    def load(cls, path: Union[Path, str], **kwargs):
        path = Path(path)
        metadata = LearnerMetadata.read(path).to_dict()
        network_parameters = ModelParams(**metadata["network_parameters"])
        lib = tvm.runtime.load_module(path / TVM_FILENAMES["engine"])
        target_device = metadata["target"]
        input_names = metadata["input_names"]
        return cls.from_runtime_module(
            network_parameters=network_parameters,
            lib=lib,
            target_device=target_device,
            input_names=input_names,
        )

    @classmethod
    def from_runtime_module(
        cls,
        network_parameters: ModelParams,
        lib: Module,
        target_device: str,
        input_names: List[str],
    ):
        dev = tvm.device(str(target_device), 0)
        graph_executor_module = GraphModule(lib["default"](dev))
        return cls(
            network_parameters=network_parameters,
            graph_executor_module=graph_executor_module,
            input_names=input_names,
            lib=lib,
            target=target_device,
        )


class PytorchApacheTVMInferenceLearner(
    ApacheTVMInferenceLearner, PytorchBaseInferenceLearner
):
    def predict(self, *input_tensors: torch.Tensor) -> Tuple[torch.Tensor]:
        device = self._convert_device(input_tensors[0].get_device())
        input_arrays = (
            input_tensor.cpu().detach().numpy()
            for input_tensor in input_tensors
        )
        output_arrays = self._predict_array(input_arrays)
        return tuple(
            torch.from_numpy(output_array).to(device)
            for output_array in output_arrays
        )

    @staticmethod
    def _convert_device(device: Any):
        if isinstance(device, int):
            return "cpu"
        return device


class TensorflowApacheTVMInferenceLearner(
    ApacheTVMInferenceLearner, TensorflowBaseInferenceLearner
):
    def predict(self, *input_tensors: tf.Tensor) -> Tuple[tf.Tensor]:
        input_arrays = (input_tensor.numpy() for input_tensor in input_tensors)
        output_arrays = self._predict_array(input_arrays)
        return tuple(
            tf.convert_to_tensor(output_array)
            for output_array in output_arrays
        )


TVM_INFERENCE_LEARNERS: Dict[
    DeepLearningFramework, Type[ApacheTVMInferenceLearner]
] = {
    DeepLearningFramework.PYTORCH: PytorchApacheTVMInferenceLearner,
    DeepLearningFramework.TENSORFLOW: TensorflowApacheTVMInferenceLearner,
}