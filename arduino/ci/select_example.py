# PlatformIO pre-script: build one example sketch per environment.
Import("env")  # noqa: F821
import os

example = env.GetProjectOption("custom_example")  # noqa: F821
path = os.path.normpath(os.path.join(env.subst("$PROJECT_DIR"), "..", "examples", example))  # noqa: F821
if not os.path.isdir(path):
    raise SystemExit("example not found: " + path)
env.Replace(PROJECT_SRC_DIR=path)  # noqa: F821
