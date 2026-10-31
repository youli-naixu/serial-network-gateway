cmake_minimum_required(VERSION 3.16)
set(PROJECT_VER "0.1.0-experimental")
# PlatformIO's unpacked IDF must not inherit an enclosing project's unborn HEAD.
set(ENV{GIT_CEILING_DIRECTORIES} "${CMAKE_CURRENT_SOURCE_DIR}")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(serial_network_gateway)
