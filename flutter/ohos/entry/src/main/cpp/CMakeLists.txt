# the minimum version of CMake.
cmake_minimum_required(VERSION 3.5.0)
project(video_surface_bridge)

set(NATIVERENDER_ROOT_PATH ${CMAKE_CURRENT_SOURCE_DIR})

if(DEFINED PACKAGE_FIND_FILE)
    include(${PACKAGE_FIND_FILE})
endif()

include_directories(${NATIVERENDER_ROOT_PATH})

add_library(video_surface_bridge SHARED napi_init.cpp)

target_compile_options(video_surface_bridge PRIVATE -std=c++17 -Wall -fvisibility=hidden)

# liblibrustdesk.so is deliberately NOT linked here: napi_init.cpp resolves the
# two surface entry points at runtime with dlopen/dlsym, so the UI still boots
# and reports codes instead of crashing when the core library is unusable.
target_link_libraries(video_surface_bridge PUBLIC
    libace_napi.z.so
    libhilog_ndk.z.so)
