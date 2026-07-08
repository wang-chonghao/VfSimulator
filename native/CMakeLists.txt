# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.

cmake_minimum_required(VERSION 3.20)
project(VfSimulatorNative LANGUAGES CXX)

include(CTest)

if(NOT BUILD_TESTING)
  return()
endif()

add_executable(vfsim_native_smoke_test
  VfsimNativeSmokeTest.cpp
  Json.cpp
  ParamDB.cpp
  ISATraits.cpp
  ProgramAnalysis.cpp
  ProgramFlatten.cpp
  IFU.cpp
  IDU.cpp
  OOO.cpp
  SimulatorRunner.cpp
)

target_include_directories(vfsim_native_smoke_test PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/..
)

target_compile_features(vfsim_native_smoke_test PRIVATE cxx_std_17)

target_compile_definitions(vfsim_native_smoke_test PRIVATE
  VFSIM_SOURCE_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/.."
)

set_target_properties(vfsim_native_smoke_test PROPERTIES
  FOLDER "Tests"
)

add_test(NAME vfsim_native_smoke_test
  COMMAND vfsim_native_smoke_test
)

add_executable(vfsim_tadd_tmul_trace
  VfsimTaddTmulTrace.cpp
  Json.cpp
  ParamDB.cpp
  ISATraits.cpp
  ProgramAnalysis.cpp
  ProgramFlatten.cpp
  IFU.cpp
  IDU.cpp
  OOO.cpp
  SimulatorRunner.cpp
)

target_include_directories(vfsim_tadd_tmul_trace PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/..
)

target_compile_features(vfsim_tadd_tmul_trace PRIVATE cxx_std_17)

target_compile_definitions(vfsim_tadd_tmul_trace PRIVATE
  VFSIM_SOURCE_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/.."
)

set_target_properties(vfsim_tadd_tmul_trace PROPERTIES
  FOLDER "Tests"
)

add_test(NAME vfsim_tadd_tmul_trace
  COMMAND vfsim_tadd_tmul_trace
)

add_executable(vfsim_native_json_runner
  VfsimNativeJsonRunner.cpp
  Json.cpp
  ParamDB.cpp
  ISATraits.cpp
  ProgramAnalysis.cpp
  ProgramFlatten.cpp
  IFU.cpp
  IDU.cpp
  OOO.cpp
  SimulatorRunner.cpp
)

target_include_directories(vfsim_native_json_runner PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/..
)

target_compile_features(vfsim_native_json_runner PRIVATE cxx_std_17)

target_compile_definitions(vfsim_native_json_runner PRIVATE
  VFSIM_SOURCE_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/.."
)

set_target_properties(vfsim_native_json_runner PROPERTIES
  FOLDER "Tests"
)
