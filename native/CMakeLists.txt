# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.

cmake_minimum_required(VERSION 3.20)
project(VfSimulatorNative LANGUAGES C CXX)

if(PROJECT_IS_TOP_LEVEL)
  include(CTest)
  set(VFSIM_BUILD_TESTS_DEFAULT ON)
else()
  set(VFSIM_BUILD_TESTS_DEFAULT OFF)
endif()

option(VFSIM_BUILD_TESTS "Build native VfSimulator tests" ${VFSIM_BUILD_TESTS_DEFAULT})
option(VFSIM_ENABLE_MLIR_PLANNER "Build MLIR IR planner entry point" OFF)

set(VFSIM_NATIVE_CORE_SOURCES
  Json.cpp
  ParamDB.cpp
  ISATraits.cpp
  ProgramAnalysis.cpp
  ProgramCanonicalization.cpp
  ProgramFlatten.cpp
  IFU.cpp
  IDU.cpp
  OOO.cpp
  SimulatorRunner.cpp
)

add_library(vfsim_native_core STATIC
  ${VFSIM_NATIVE_CORE_SOURCES}
)

add_library(vfsim::native_core ALIAS vfsim_native_core)

target_include_directories(vfsim_native_core PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/..
)

target_compile_features(vfsim_native_core PUBLIC cxx_std_17)

target_compile_definitions(vfsim_native_core PUBLIC
  VFSIM_SOURCE_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/.."
)

if(VFSIM_ENABLE_MLIR_PLANNER)
  if(NOT TARGET MLIRIR)
    find_package(MLIR REQUIRED CONFIG)
  endif()

  add_library(vfsim_ir_planner STATIC
    IRPlanner.cpp
  )

  add_library(vfsim::ir_planner ALIAS vfsim_ir_planner)

  target_link_libraries(vfsim_ir_planner PUBLIC
    vfsim::native_core
    MLIRIR
    MLIRSupport
  )
  target_include_directories(vfsim_ir_planner PUBLIC
    ${MLIR_INCLUDE_DIRS}
    ${LLVM_INCLUDE_DIRS}
  )
endif()

if(NOT VFSIM_BUILD_TESTS)
  return()
endif()

if(PROJECT_IS_TOP_LEVEL AND NOT BUILD_TESTING)
  return()
endif()

add_executable(vfsim_native_smoke_test
  VfsimNativeSmokeTest.cpp
)

target_link_libraries(vfsim_native_smoke_test PRIVATE
  vfsim::native_core
)

set_target_properties(vfsim_native_smoke_test PROPERTIES
  FOLDER "Tests"
)

add_test(NAME vfsim_native_smoke_test
  COMMAND vfsim_native_smoke_test
)

add_executable(vfsim_tadd_tmul_trace
  VfsimTaddTmulTrace.cpp
)

target_link_libraries(vfsim_tadd_tmul_trace PRIVATE
  vfsim::native_core
)

set_target_properties(vfsim_tadd_tmul_trace PROPERTIES
  FOLDER "Tests"
)

add_test(NAME vfsim_tadd_tmul_trace
  COMMAND vfsim_tadd_tmul_trace
)

add_executable(vfsim_program_canonicalization_test
  ProgramCanonicalizationTest.cpp
)

target_link_libraries(vfsim_program_canonicalization_test PRIVATE
  vfsim::native_core
)

add_test(NAME vfsim_program_canonicalization_test
  COMMAND vfsim_program_canonicalization_test
)

add_executable(vfsim_native_json_runner
  VfsimNativeJsonRunner.cpp
)

target_link_libraries(vfsim_native_json_runner PRIVATE
  vfsim::native_core
)

set_target_properties(vfsim_native_json_runner PROPERTIES
  FOLDER "Tests"
)
