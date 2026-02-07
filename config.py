import logging
import os
import yaml

from value_types import WalkAssistantValueTypes


class WalkAssistantConfig:
    __config = {
        "auto_start_osc": True,
        "bind_address": "",
        "bind_port": 9000,
        "input_smoothing": 0.8,
        "walk_threshold": 150,
        "run_threshold": 400,
        "walk_key": "w",
        "run_key": "shift",
        "endpoint_groups": [
            {
                "id": 0,
                "alias": "Default",
                "value_type": str(WalkAssistantValueTypes.VECTOR3),
                "endpoints": [
                    {
                        "alias": "primary",
                        "resource": "/accelerometer",
                        "value_type": str(WalkAssistantValueTypes.VECTOR3),
                        "id": 0,
                    },
                ],
            },
            {
                "id": 1,
                "alias": "Default",
                "value_type": str(WalkAssistantValueTypes.VECTOR3),
                "endpoints": [
                    {
                        "alias": "test",
                        "resource": "/test",
                        "value_type": str(WalkAssistantValueTypes.VECTOR3),
                        "id": 1,
                    },
                    {
                        "alias": "test2",
                        "resource": "/test2",
                        "value_type": str(WalkAssistantValueTypes.FLOAT),
                        "id": 2,
                    },
                ],
            },
        ],
    }
    __config_path = ""
    __config_logger = logging.getLogger("WA_Config")
    __config_logger.setLevel(logging.DEBUG)

    def __init__(self, config_file_path: str):
        WalkAssistantConfig.__config_path = config_file_path
        if not os.path.isfile(config_file_path):
            self.__config_logger.info(
                f"Creating default config file: {config_file_path}"
            )
            with open(config_file_path, "w") as yaml_file:
                yaml.safe_dump(self.__config, yaml_file, sort_keys=False)
        else:
            self.__config_logger.info(f"Loading config file: {config_file_path}")
            loaded_config = yaml.safe_load(open(config_file_path))
            try:
                if "endpoint_groups" in loaded_config and len(loaded_config.keys()) > 8:
                    self.__config = loaded_config
            except Exception:
                self.__config_logger.error(
                    "Config file is invalid, using default config"
                )

    @staticmethod
    def config(name):
        WalkAssistantConfig.__config_logger.debug(
            f"Retrieving config value for '{name}'"
        )
        return WalkAssistantConfig.__config[name]

    @staticmethod
    def set(name: str | list[str], value):
        names = name if isinstance(name, list) else [name]
        values = value if isinstance(value, list) else [value]
        if len(names) != len(values):
            WalkAssistantConfig.__config_logger.error(
                f"Number of names ({len(names)}) does not match number of values ({len(values)})"
            )
            raise ValueError(
                f"Number of names ({len(names)}) does not match number of values ({len(values)})"
            )
        for i, n in enumerate(names):
            if n in WalkAssistantConfig.__config.keys():
                WalkAssistantConfig.__config_logger.debug(
                    f"Setting config value for '{n}' to '{values[i]}'"
                )
                WalkAssistantConfig.__config[n] = values[i]
            else:
                WalkAssistantConfig.__config_logger.error(
                    f"Key '{n}' not found in config"
                )
                raise KeyError(f"Key {n} not found in config")
        if WalkAssistantConfig.__config_path:
            yaml.safe_dump(
                WalkAssistantConfig.__config,
                open(WalkAssistantConfig.__config_path, "w"),
            )
        return True

    @staticmethod
    def set_dict(config_dict: dict):
        for k, v in config_dict.items():
            if k in WalkAssistantConfig.__config.keys():
                WalkAssistantConfig.set(k, v)
            else:
                WalkAssistantConfig.__config_logger.error(
                    f"Key '{k}' not found in config"
                )
                raise KeyError(f"Key {k} not found in config")
        if WalkAssistantConfig.__config_path:
            yaml.safe_dump(
                WalkAssistantConfig.__config,
                open(WalkAssistantConfig.__config_path, "w"),
            )
        return True
