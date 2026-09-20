cmake_minimum_required(VERSION 3.22...3.30)
project(wormhole_external_validation LANGUAGES CXX)

if(NOT TARGET TT::Metalium)
    find_package(TT-Metalium REQUIRED)
endif()

add_executable(wormhole_external_validation host/wormhole_external_validation.cpp)
target_compile_features(wormhole_external_validation PRIVATE cxx_std_20)
target_link_libraries(wormhole_external_validation PRIVATE TT::Metalium)

# Device sources are passed to Metalium's runtime compiler by the host program.
target_compile_definitions(
    wormhole_external_validation
    PRIVATE WORMHOLE_EXTERNAL_SOURCE_ROOT="producer"
)
