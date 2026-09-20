// SPDX-License-Identifier: Apache-2.0
#include <tt-metalium/host_api.hpp>

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {
constexpr std::uint32_t kMaximumRepetitions = 64;
constexpr std::uint32_t kMaximumOutputBytes = 16 * 1024 * 1024;
constexpr std::array<std::string_view, 8> kFlags = {
    "--recipe",          "--input",              "--functional-output", "--manifest-output",
    "--repetitions",     "--warmup-repetitions", "--timeout-seconds",    "--max-output-bytes",
};
constexpr std::array<std::string_view, 3> kRecipes = {
    "noc_ack_roundtrip_v1", "dram_read_return_v1", "compute_service_v1"};

struct Invocation {
    std::string recipe;
    std::filesystem::path input;
    std::filesystem::path functional_output;
    std::filesystem::path manifest_output;
    std::uint32_t repetitions;
    std::uint32_t warmups;
    std::uint32_t timeout_seconds;
    std::uint32_t max_output_bytes;
};

std::uint32_t parse_positive(std::string_view value, std::string_view label) {
    std::uint32_t result = 0;
    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size() || result == 0) {
        throw std::invalid_argument(std::string(label) + " must be a positive integer");
    }
    return result;
}

std::uint32_t parse_nonnegative(std::string_view value, std::string_view label) {
    if (value == "0") {
        return 0;
    }
    return parse_positive(value, label);
}

void require_portable_path(const std::filesystem::path& path) {
    if (path.empty() || path.is_absolute()) {
        throw std::invalid_argument("capture paths must be nonempty and relative");
    }
    for (const auto& component : path) {
        if (component == "..") {
            throw std::invalid_argument("capture paths cannot escape the kit");
        }
    }
}

Invocation parse_invocation(int argc, char** argv) {
    if (argc != 18) {
        throw std::invalid_argument("expected the fixed eight-argument capture interface");
    }
    std::map<std::string_view, std::string_view> values;
    for (std::size_t index = 0; index < kFlags.size(); ++index) {
        if (argv[2 * index + 1] != kFlags[index]) {
            throw std::invalid_argument("capture flags must use the fixed order");
        }
        values.emplace(kFlags[index], argv[2 * index + 2]);
    }
    const std::string recipe(values.at("--recipe"));
    if (std::find(kRecipes.begin(), kRecipes.end(), recipe) == kRecipes.end()) {
        throw std::invalid_argument("unknown capture recipe");
    }
    Invocation result{
        .recipe = recipe,
        .input = std::string(values.at("--input")),
        .functional_output = std::string(values.at("--functional-output")),
        .manifest_output = std::string(values.at("--manifest-output")),
        .repetitions = parse_positive(values.at("--repetitions"), "repetitions"),
        .warmups = parse_nonnegative(values.at("--warmup-repetitions"), "warmups"),
        .timeout_seconds = parse_positive(values.at("--timeout-seconds"), "timeout"),
        .max_output_bytes = parse_positive(values.at("--max-output-bytes"), "output budget"),
    };
    require_portable_path(result.input);
    require_portable_path(result.functional_output);
    require_portable_path(result.manifest_output);
    if (result.repetitions > kMaximumRepetitions || result.warmups >= result.repetitions ||
        result.max_output_bytes > kMaximumOutputBytes) {
        throw std::invalid_argument("capture invocation exceeds the producer's finite limits");
    }
    return result;
}

void require_environment() {
    const std::array<std::pair<const char*, const char*>, 2> required = {{
        {"TT_METAL_SLOW_DISPATCH_MODE", "1"}, {"TT_METAL_DISABLE_SFPLOADMACRO", "1"}}};
    for (const auto& [name, expected] : required) {
        const char* value = std::getenv(name);
        if (value == nullptr || std::string_view(value) != expected) {
            throw std::runtime_error(std::string(name) + " must match the capture manifest");
        }
    }
}
}  // namespace

int main(int argc, char** argv) {
    try {
        const Invocation invocation = parse_invocation(argc, argv);
        require_environment();
        if (!std::filesystem::is_regular_file(invocation.input)) {
            throw std::runtime_error("declared case input is unavailable");
        }

        // The pinned worker adds the recipe-specific host setup for the three checked
        // device kernels before this target is admitted. Refuse to emit evidence until
        // that build-time integration defines the completion contract.
#ifndef WORMHOLE_EXTERNAL_RECIPE_DISPATCH_V1
        throw std::runtime_error("WORMHOLE_EXTERNAL_RECIPE_DISPATCH_V1 is not linked");
#else
        return wormhole_external_recipe_dispatch_v1(invocation);
#endif
    } catch (const std::exception& error) {
        std::fprintf(stderr, "wormhole external validation failed: %s\n", error.what());
        return 2;
    }
}
