cmake_minimum_required(VERSION 3.21)
project(machina_trinity LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(MACHINA_USE_CUDA "Enable CUDA backend (optional)" OFF)

# JSON backend (json-c)
find_package(PkgConfig REQUIRED)
pkg_check_modules(JSONC REQUIRED json-c)


# --- Core library (pure engine, no tools) ---
add_library(machina_core_lib
    core/src/types.cpp
    core/src/ids.cpp
    core/src/hash.cpp
    core/src/crypto.cpp
    core/src/proc.cpp
    core/src/selector.cpp
    core/src/selector_external.cpp
    core/src/state.cpp
    core/src/tx.cpp
    core/src/log.cpp
    core/src/registry.cpp
    core/src/plugin_loader.cpp
    core/src/tools.cpp
    core/src/cts.cpp
    core/src/embedding.cpp
    core/src/embedding_provider.cpp
    core/src/gpu_context.cpp
    core/src/selector_gpu.cpp
    core/src/wal.cpp
    core/src/sandbox.cpp
    core/src/lease.cpp
    core/src/serialization.cpp
    core/src/goal_registry.cpp
    core/src/config.cpp
)

target_include_directories(machina_core_lib PUBLIC core/include)
target_include_directories(machina_core_lib PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_include_directories(machina_core_lib PUBLIC ${JSONC_INCLUDE_DIRS})
target_link_directories(machina_core_lib PUBLIC ${JSONC_LIBRARY_DIRS})
target_link_libraries(machina_core_lib PUBLIC ${JSONC_LIBRARIES})

if (NOT WIN32)
  target_link_libraries(machina_core_lib PUBLIC dl pthread)
endif()


# --- Tier-0 tools (built-in tool implementations) ---
add_library(machina_tier0
    tools/tier0/error_scan.cpp
    tools/tier0/fs_tools.cpp
    tools/tier0/shell_exec.cpp
    tools/tier0/http_get.cpp
    tools/tier0/queue_tools.cpp
    tools/tier0/memory_append.cpp
    tools/tier0/memory_query.cpp
    tools/tier0/embed_tools.cpp
    tools/tier0/vectordb_tools.cpp
    tools/tier0/runlog_tools.cpp
    tools/tier0/report_summary.cpp
    tools/tier0/meta.cpp
    tools/tier0/gpu_smoke.cpp
    tools/tier0/gpu_metrics.cpp
    tools/tier0/proc_metrics.cpp
    tools/tier0/genesis.cpp
)

target_link_libraries(machina_tier0 PUBLIC machina_core_lib)


# --- Backward-compatible aggregate target ---
# All existing targets that link machina_core get both core + tier0.
add_library(machina_core INTERFACE)
target_link_libraries(machina_core INTERFACE machina_tier0)


# Embed RPATH so executables can find json-c at runtime without LD_LIBRARY_PATH.
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE)
set(CMAKE_BUILD_RPATH_USE_ORIGIN TRUE)


add_executable(machina_cli
    runner/main.cpp
    runner/runner_utils.cpp
    runner/cmd_run.cpp
    runner/cmd_replay.cpp
    runner/cmd_autopilot.cpp
    runner/cmd_serve.cpp
    runner/cmd_chat.cpp
    runner/tool_setup.cpp
)
target_link_libraries(machina_cli PRIVATE machina_core)

add_executable(machina_toolhost toolhost/main.cpp)
target_link_libraries(machina_toolhost PRIVATE machina_core)

# warnings
if (MSVC)
  target_compile_options(machina_core_lib PRIVATE /W4)
  target_compile_options(machina_tier0 PRIVATE /W4)
  target_compile_options(machina_cli PRIVATE /W4)
  target_compile_options(machina_toolhost PRIVATE /W4)
else()
  target_compile_options(machina_core_lib PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
  target_compile_options(machina_tier0 PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
  target_compile_options(machina_cli PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
  target_compile_options(machina_toolhost PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
endif()


if (MACHINA_USE_CUDA)
  enable_language(CUDA)
  target_compile_definitions(machina_core_lib PUBLIC MACHINA_USE_CUDA)
  target_sources(machina_core_lib PRIVATE core/cuda/centroid_kernel.cu)
  set_target_properties(machina_core_lib PROPERTIES CUDA_STANDARD 14)
endif()

# -----------------
# Tests (ctest)
# -----------------
include(CTest)

if (BUILD_TESTING)
  add_executable(test_cpq tests/test_cpq.cpp)
  target_link_libraries(test_cpq PRIVATE machina_core)

  add_executable(test_wal tests/test_wal.cpp)
  target_link_libraries(test_wal PRIVATE machina_core)

  add_executable(test_tx tests/test_tx.cpp)
  target_link_libraries(test_tx PRIVATE machina_core)
  
  add_executable(test_tx_patch_apply tests/test_tx_patch_apply.cpp)
  target_link_libraries(test_tx_patch_apply PRIVATE machina_core)

  add_executable(test_memory tests/test_memory.cpp)
  target_link_libraries(test_memory PRIVATE machina_core)

  add_executable(test_memory_query tests/test_memory_query.cpp)
  target_link_libraries(test_memory_query PRIVATE machina_core)

  add_test(NAME cpq COMMAND test_cpq)
  add_test(NAME wal COMMAND test_wal)
  add_test(NAME tx COMMAND test_tx)
  add_test(NAME tx_patch_apply COMMAND test_tx_patch_apply)
  add_test(NAME memory COMMAND test_memory)
  add_test(NAME memory_query COMMAND test_memory_query)

  add_library(test_tool_plugin SHARED tests/plugins/test_tool_plugin.cpp)
  target_link_libraries(test_tool_plugin PRIVATE machina_core)

  add_executable(test_toolhost tests/test_toolhost.cpp)
  target_link_libraries(test_toolhost PRIVATE machina_core)

  add_test(NAME toolhost COMMAND test_toolhost $<TARGET_FILE:machina_toolhost> $<TARGET_FILE:test_tool_plugin>)

  add_executable(test_goal_registry tests/test_goal_registry.cpp)
  target_link_libraries(test_goal_registry PRIVATE machina_core)

  add_test(NAME goal_registry COMMAND test_goal_registry)

  add_executable(test_input_safety tests/test_input_safety.cpp runner/runner_utils.cpp)
  target_include_directories(test_input_safety PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/runner)
  target_link_libraries(test_input_safety PRIVATE machina_core)

  add_test(NAME input_safety COMMAND test_input_safety)

  add_executable(test_sandbox tests/test_sandbox.cpp)
  target_link_libraries(test_sandbox PRIVATE machina_core)
  add_test(NAME sandbox COMMAND test_sandbox)

  add_executable(test_lease tests/test_lease.cpp)
  target_link_libraries(test_lease PRIVATE machina_core)
  add_test(NAME lease COMMAND test_lease)

  add_executable(test_wal_rotation tests/test_wal_rotation.cpp)
  target_link_libraries(test_wal_rotation PRIVATE machina_core)
  add_test(NAME wal_rotation COMMAND test_wal_rotation)

  add_executable(test_config tests/test_config.cpp)
  target_link_libraries(test_config PRIVATE machina_core)
  add_test(NAME config COMMAND test_config)

  add_executable(test_plugin_hash tests/test_plugin_hash.cpp)
  target_link_libraries(test_plugin_hash PRIVATE machina_core)
  add_test(NAME plugin_hash COMMAND test_plugin_hash)
endif()
