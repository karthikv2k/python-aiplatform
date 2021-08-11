# -*- coding: utf-8 -*-

# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import inspect
from typing import Any
from typing import Dict
from typing import Tuple

import ast
from collections import namedtuple

Import = namedtuple("Import", ["module", "name", "alias"])


def get_imports(path):
    with open(path) as fh:
        root = ast.parse(fh.read(), path)

    for node in ast.iter_child_nodes(root):
        if isinstance(node, ast.Import):
            module = []
        elif isinstance(node, ast.ImportFrom):
            module = node.module.split(".")
        else:
            continue

        for n in node.names:
            yield Import(module, n.name.split("."), n.asname)


class SourceMaker:
    def __init__(self, cls_name: str):
        self.source = ["class {}(torch.nn.Module):".format(cls_name)]

    def add_method(self, method_str: str):
        self.source.extend(method_str.split("\n"))


def _make_class_source(obj: Any) -> str:
    """Retrieves the source code for the class obj represents, usually an extension
       of VertexModel.

    Args:
        obj (Any): An instantiation of a user-written class
    """
    source_maker = SourceMaker(obj.__class__.__name__)

    for key, value in inspect.getmembers(obj):
        if (inspect.ismethod(value) or inspect.isfunction(value)) and value.__repr__()[
            len("<bound method ") :
        ].startswith(obj.__class__.__name__):
            source_maker.add_method(inspect.getsource(value))

    source_maker.add_method(inspect.getsource(obj.fit))
    source_maker.add_method(inspect.getsource(obj.predict))

    return "\n".join(source_maker.source)


def _make_source(
    cls_source: str,
    cls_name: str,
    instance_method: str,
    pass_through_params: Dict[str, str],
    param_name_to_serialized_info: Dict[str, Tuple[str, type]],
    obj: Any,
) -> str:
    """Converts a class source to a string including necessary imports.

    Args:
        cls_source (str): A string representing the source code of a user-written class.
        cls_name (str): The name of the class cls_source represents.
        instance_method (str): The method within the class that should be called from __main__
        pass_through_params (dict[str, Any]): A dictionary mapping primitive parameter names to their values
        param_name_to_serialize_info (dict[str, A]): A dictionary mapping a parameter that needed
                                                     to be serialized to its URI and value type.

    Returns:
        A string representing a user-written class that can be written to a file in
        order to yield an inner script for the ModelBuilder SDK. The only difference
        between the user-written code and the string returned by this method is that
        the user has the option to specify a method to call from __main__.
    """

    module = inspect.getmodule(obj.__class__)
    imports = get_imports(module)

    for my_import in imports:
        if len(my_import[1][0]) > 0:
            src = src + "from "
            modules = my_import[1][0]

            for module in modules:
                src = src + module + "."

            src = src[:-1]

        if len(my_import[1][1]) > 0:
            src = src + " import "
            import_list = my_import[1][1]

            for import_item in import_list:
                src = src + import_item + "."

            src = src[:-1]

        if len(my_import[1][2]) > 0:
            src = src + " as "
            aliases = my_import[1][2]

            for alias in aliases:
                src = src + alias + "."

            src = src[:-1]

        src = src + "\n"

    # Hard-coded specific files as imports because (for now) all data serialization methods
    # come from one of two files and we do not retrieve the modules for the methods at this
    # moment.
    src = "\n".join(
        [
            "import os",
            #    "import torch",
            #    "import pandas as pd",
            "from google.cloud.aiplatform.experimental.vertex_model import base",
            "from google.cloud.aiplatform.experimental.vertex_model.serializers.pandas import _deserialize_dataframe",
            "from google.cloud.aiplatform.experimental.vertex_model.serializers.pytorch import _deserialize_dataloader",
            "from google.cloud.aiplatform.experimental.vertex_model.serializers import model",
            cls_source,
        ]
    )

    # Then, instantiate model
    # First, grab args and kwargs using the _constructor_arguments variable in VertexModel
    class_args = inspect.signature(obj.__class__.__init__).bind(
        obj, *obj._constructor_arguments[0], **obj._constructor_arguments[1]
    )

    # need to index pass the first arg to avoid the call to self.
    src = src + f"my_model = {cls_name}({','.join(map(str, class_args.args[1:]))})\n"

    if instance_method is not None:
        # Start function call
        src = src + f"my_model.{instance_method}("

        # Iterate through parameters.
        # We are currently working around not including the _serialization_mapping
        # with our generated source and assume the serializer/deserializer is in
        # our serializer module.
        for (
            parameter_name,
            (parameter_uri, parameter_type),
        ) in param_name_to_serialized_info.items():
            print(obj._data_serialization_mapping.keys())
            deserializer = obj._data_serialization_mapping[parameter_type][0]

            # Can also make individual calls for each serialized parameter, but was unsure
            # for situations such as when a dataloader format is serialized.
            src = src + f"{parameter_name}={deserializer.__name__}('{parameter_uri}'), "

        for parameter_name, parameter_value in pass_through_params.items():
            if type(parameter_value) is str:
                src = src + f"{parameter_name}='{parameter_value}', "
            else:
                src = src + f"{parameter_name}={parameter_value}, "

        src = src + ")\n"

    src = (
        src
        + "model._serialize_local_model(os.getenv('AIP_MODEL_DIR'), my_model, my_model.training_mode)"
    )

    return src
