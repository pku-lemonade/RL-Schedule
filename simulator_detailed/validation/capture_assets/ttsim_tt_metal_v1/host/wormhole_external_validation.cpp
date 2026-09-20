// SPDX-License-Identifier: Apache-2.0
#include "sha256.hpp"

#include <nlohmann/json.hpp>
#include <tt-metalium/allocator.hpp>
#include <tt-metalium/bfloat16.hpp>
#include <tt-metalium/circular_buffer_config.hpp>
#include <tt-metalium/constants.hpp>
#include <tt-metalium/distributed.hpp>
#include <tt-metalium/host_api.hpp>
#include <tt-metalium/mesh_device.hpp>
#include <tt-metalium/tensor_accessor_args.hpp>
#include <tt-metalium/tilize_utils.hpp>
#include <tt-metalium/tt_metal.hpp>

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <map>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {
using json = nlohmann::json;
using namespace tt;
using namespace tt::tt_metal;
namespace dist = tt::tt_metal::distributed;

constexpr std::uint32_t kMaximumRepetitions = 64;
constexpr std::uint32_t kMaximumOutputBytes = 16 * 1024 * 1024;
constexpr std::uint32_t kMaximumTransferBytes = 4096;
constexpr std::uint32_t kCompletionNoc = 0x57484f4c;
constexpr std::uint32_t kCompletionDram = 0x4452414d;
constexpr std::string_view kCompletionMarker = "WORMHOLE_EXTERNAL_COMPLETE_V1";
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

struct CampaignContext {
    json campaign;
    json case_record;
    json producer;
    json build;
    json input;
    json workload;
    json mapping;
};

struct DeviceSession {
    std::shared_ptr<dist::MeshDevice> mesh;
    IDevice* device;

    DeviceSession() : mesh(dist::MeshDevice::create_unit_mesh(0)), device(mesh->get_devices().at(0)) {}
    ~DeviceSession() {
        if (mesh) {
            try {
                mesh->close();
            } catch (...) {
            }
        }
    }
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
    if (argc != 17) {
        throw std::invalid_argument("expected the fixed eight-argument capture interface");
    }
    std::map<std::string_view, std::string_view> values;
    for (std::size_t index = 0; index < kFlags.size(); ++index) {
        if (std::string_view(argv[2 * index + 1]) != kFlags[index]) {
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
    const std::array<std::pair<const char*, const char*>, 4> required = {{
        {"TT_METAL_HOME", "vendor/tt-metal"},
        {"TT_METAL_SIMULATOR", "runtime/ttsim/libttsim_wh.so"},
        {"TT_METAL_SLOW_DISPATCH_MODE", "1"},
        {"TT_METAL_DISABLE_SFPLOADMACRO", "1"},
    }};
    for (const auto& [name, expected] : required) {
        const char* value = std::getenv(name);
        if (value == nullptr || std::string_view(value) != expected) {
            throw std::runtime_error(std::string(name) + " must match the capture manifest");
        }
    }
}

std::vector<std::uint8_t> read_bytes(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("cannot read declared input: " + path.string());
    }
    return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}

json read_json(const std::filesystem::path& path) {
    const auto bytes = read_bytes(path);
    return json::parse(bytes.begin(), bytes.end());
}

std::uint32_t unsigned_field(const json& record, std::string_view name) {
    const auto& value = record.at(std::string(name));
    if (!value.is_number_unsigned() && !value.is_number_integer()) {
        throw std::runtime_error(std::string(name) + " must be an unsigned integer");
    }
    const auto result = value.get<std::int64_t>();
    if (result < 0 || result > std::numeric_limits<std::uint32_t>::max()) {
        throw std::runtime_error(std::string(name) + " is outside the supported range");
    }
    return static_cast<std::uint32_t>(result);
}

std::array<std::uint32_t, 2> core_field(const json& record, std::string_view name) {
    const auto& value = record.at(std::string(name));
    if (!value.is_array() || value.size() != 2) {
        throw std::runtime_error(std::string(name) + " must contain two coordinates");
    }
    return {unsigned_field(json{{"value", value.at(0)}}, "value"),
            unsigned_field(json{{"value", value.at(1)}}, "value")};
}

json derive_workload(const json& input, const std::string& family) {
    if (family == "noc_ack_roundtrip") {
        return {{"bytes", input.at("bytes")},
                {"completion", input.at("completion")},
                {"count", input.at("count")},
                {"destination_address", input.at("destination_address")},
                {"operation", "remote_l1_write"},
                {"pattern_seed", input.at("pattern_seed")},
                {"pattern_stride", input.at("pattern_stride")}};
    }
    if (family == "dram_read_return") {
        return {{"address", input.at("address")},
                {"bytes", input.at("bytes")},
                {"completion", input.at("completion")},
                {"count", input.at("count")},
                {"operation", "dram_read"},
                {"pattern_seed", input.at("pattern_seed")},
                {"pattern_stride", input.at("pattern_stride")}};
    }
    return {{"data_type", input.at("data_type")},
            {"fidelity", input.at("fidelity")},
            {"input_layout", input.at("input_layout")},
            {"operation", "matmul"},
            {"output_address", input.at("output_address")},
            {"output_bytes", input.at("output_bytes")},
            {"output_layout", input.at("output_layout")},
            {"sentinel_elements", input.at("sentinel_elements")},
            {"sentinel_seed", input.at("sentinel_seed")},
            {"shape", input.at("shape")},
            {"work", input.at("work")}};
}

json derive_mapping(const json& input, const std::string& family) {
    if (family == "noc_ack_roundtrip") {
        return {{"destination", input.at("destination")},
                {"destination_core", input.at("destination_core")},
                {"fabric_id", input.at("fabric_id")},
                {"source", input.at("source")},
                {"source_core", input.at("source_core")}};
    }
    if (family == "dram_read_return") {
        return {{"destination", input.at("destination")},
                {"destination_core", input.at("destination_core")},
                {"dram_bank", input.at("dram_bank")},
                {"resource_id", input.at("resource_id")}};
    }
    return {{"output_resource", "worker-0-l1"},
            {"resource", "worker-0-tensix"},
            {"worker", "worker-0"},
            {"worker_core", input.at("worker_core")}};
}

std::string family_for_recipe(const std::string& recipe) {
    if (recipe == "noc_ack_roundtrip_v1") return "noc_ack_roundtrip";
    if (recipe == "dram_read_return_v1") return "dram_read_return";
    return "compute_service";
}

void validate_input_contract(const json& input, const std::string& family) {
    const std::map<std::string, std::vector<std::string>> fields = {
        {"noc_ack_roundtrip", {"bytes", "case_family", "completion", "count", "destination",
                               "destination_address", "destination_core", "fabric_id", "pattern_seed",
                               "pattern_stride", "source", "source_core"}},
        {"dram_read_return", {"address", "bytes", "case_family", "completion", "count", "destination",
                              "destination_core", "dram_bank", "pattern_seed", "pattern_stride", "resource_id"}},
        {"compute_service", {"case_family", "completion", "data_type", "fidelity", "input_layout",
                             "output_address", "output_bytes", "output_layout", "sentinel_elements",
                             "sentinel_seed", "shape", "work", "worker_core"}},
    };
    if (!input.is_object() || input.value("case_family", "") != family || input.size() != fields.at(family).size()) {
        throw std::runtime_error("case input does not match the selected finite recipe");
    }
    for (const auto& name : fields.at(family)) {
        if (!input.contains(name)) throw std::runtime_error("case input is missing " + name);
    }
    if (family == "noc_ack_roundtrip") {
        if (input.at("completion") != "returned_acknowledgement") throw std::runtime_error("unsupported NoC completion");
        core_field(input, "source_core");
        core_field(input, "destination_core");
    } else if (family == "dram_read_return") {
        if (input.at("completion") != "worker_visible") throw std::runtime_error("unsupported DRAM completion");
        core_field(input, "destination_core");
    } else {
        if (input.at("completion") != "resource_release" || input.at("data_type") != "bf16" ||
            input.at("fidelity") != "hifi2" || input.at("input_layout") != "tile" ||
            input.at("output_layout") != "tile" || input.at("shape") != json::array({32, 32, 32}) ||
            unsigned_field(input, "work") != 65536 || unsigned_field(input, "output_bytes") != 2048) {
            throw std::runtime_error("compute recipe requires one 32x32x32 BF16 HiFi2 tile");
        }
        core_field(input, "worker_core");
        if (unsigned_field(input, "sentinel_elements") > 1024) throw std::runtime_error("compute sentinel exceeds output");
    }
}

CampaignContext load_context(const Invocation& invocation) {
    const auto input_bytes = read_bytes(invocation.input);
    const json input = json::parse(input_bytes.begin(), input_bytes.end());
    const json campaign = read_json("campaign.json");
    const std::string family = family_for_recipe(invocation.recipe);
    validate_input_contract(input, family);
    const std::string input_sha = wormhole_external::sha256(input_bytes);

    std::vector<json> matches;
    for (const auto& candidate : campaign.at("cases")) {
        const auto& identity = candidate.at("simulator_input");
        if (candidate.at("family") == family && identity.at("sha256") == input_sha &&
            unsigned_field(identity, "size_bytes") == input_bytes.size()) {
            matches.push_back(candidate);
        }
    }
    if (matches.size() != 1) throw std::runtime_error("case input does not identify exactly one campaign case");
    json case_record = matches.front();
    const json workload = derive_workload(input, family);
    const json mapping = derive_mapping(input, family);
    if (json::parse(case_record.at("conditions").at("workload").at("value").at("text").get<std::string>()) != workload ||
        json::parse(case_record.at("conditions").at("mapping").at("value").at("text").get<std::string>()) != mapping) {
        throw std::runtime_error("case input disagrees with canonical campaign conditions");
    }
    const auto& samples = case_record.at("boundary_maps").at(0).at("samples");
    if (samples.at("repetition_ids").size() != invocation.repetitions ||
        samples.at("warmup_repetition_ids").size() != invocation.warmups ||
        unsigned_field(case_record.at("budget"), "repetitions") != invocation.repetitions ||
        unsigned_field(case_record.at("budget"), "warmup_repetitions") != invocation.warmups ||
        unsigned_field(case_record.at("budget"), "max_output_bytes") != invocation.max_output_bytes) {
        throw std::runtime_error("invocation disagrees with campaign budgets");
    }

    std::vector<json> producers;
    for (const auto& candidate : campaign.at("producers")) {
        if (candidate.at("adapter") == "ttsim_tt_metal_v1") producers.push_back(candidate);
    }
    if (producers.size() != 1) throw std::runtime_error("campaign requires one named ttsim producer");
    json producer = producers.front();
    bool bound = false;
    for (const auto& binding : case_record.at("producers")) {
        bound = bound || binding.at("producer_id") == producer.at("producer_id");
    }
    if (!bound) throw std::runtime_error("campaign case is not bound to the ttsim producer");
    std::vector<json> builds;
    for (const auto& candidate : campaign.at("builds")) {
        if (candidate.at("build_id") == producer.at("build_id")) builds.push_back(candidate);
    }
    if (builds.size() != 1) throw std::runtime_error("ttsim producer build identity is ambiguous");
    return {campaign, case_record, producer, builds.front(), input, workload, mapping};
}

std::vector<std::uint8_t> pattern(std::uint32_t size, std::uint32_t seed, std::uint32_t stride) {
    if (size == 0 || size > kMaximumTransferBytes || seed > 255 || stride == 0) {
        throw std::runtime_error("transfer pattern parameters exceed finite limits");
    }
    std::vector<std::uint8_t> result(size);
    for (std::uint32_t index = 0; index < size; ++index) result[index] = (seed + stride * index) & 0xff;
    return result;
}

void require_l1_range(IDevice* device, std::uint32_t address, std::uint32_t size) {
    if (static_cast<std::uint64_t>(address) + size > device->allocator()->get_worker_l1_size()) {
        throw std::runtime_error("declared L1 offset exceeds the selected worker memory");
    }
}

std::filesystem::path kernel_path(std::string_view name) {
    return std::filesystem::path(WORMHOLE_EXTERNAL_SOURCE_ROOT) / "kernels" / name;
}

void write_l1(IDevice* device, CoreCoord core, std::uint32_t address, const std::vector<std::uint8_t>& bytes) {
    if (!detail::WriteToDeviceL1(device, core, address, std::span<const std::uint8_t>(bytes))) {
        throw std::runtime_error("L1 write failed");
    }
}

std::vector<std::uint8_t> read_l1(IDevice* device, CoreCoord core, std::uint32_t address, std::uint32_t size) {
    std::vector<std::uint8_t> result(size);
    if (!detail::ReadFromDeviceL1(device, core, address, std::span<std::uint8_t>(result))) {
        throw std::runtime_error("L1 read failed");
    }
    return result;
}

std::vector<std::uint8_t> execute_noc(DeviceSession& session, const CampaignContext& context) {
    const auto source_coord = core_field(context.input, "source_core");
    const auto destination_coord = core_field(context.input, "destination_core");
    const CoreCoord source{source_coord[0], source_coord[1]};
    const CoreCoord destination{destination_coord[0], destination_coord[1]};
    const CoreCoord physical_destination = session.device->worker_core_from_logical_core(destination);
    const std::uint32_t base = session.device->allocator()->get_base_allocator_addr(HalMemType::L1);
    const std::uint32_t bytes = unsigned_field(context.input, "bytes");
    const std::uint32_t source_address = base;
    const std::uint32_t completion_address = base + kMaximumTransferBytes;
    const std::uint32_t destination_address = base + unsigned_field(context.input, "destination_address");
    require_l1_range(session.device, source_address, bytes);
    require_l1_range(session.device, completion_address, sizeof(std::uint32_t));
    require_l1_range(session.device, destination_address, bytes);
    const auto expected = pattern(bytes, unsigned_field(context.input, "pattern_seed"),
                                  unsigned_field(context.input, "pattern_stride"));
    write_l1(session.device, source, source_address, expected);
    write_l1(session.device, destination, destination_address, std::vector<std::uint8_t>(bytes));
    write_l1(session.device, source, completion_address, std::vector<std::uint8_t>(sizeof(std::uint32_t)));

    Program program = CreateProgram();
    const auto kernel = CreateKernel(
        program, kernel_path("noc_ack_roundtrip.cpp").string(), source,
        DataMovementConfig{.processor = DataMovementProcessor::RISCV_0, .noc = NOC::RISCV_0_default});
    SetRuntimeArgs(program, kernel, source,
                   {static_cast<std::uint32_t>(physical_destination.x),
                    static_cast<std::uint32_t>(physical_destination.y), source_address, destination_address,
                    completion_address, bytes, unsigned_field(context.input, "count")});
    detail::LaunchProgram(session.device, program);
    const auto observed = read_l1(session.device, destination, destination_address, bytes);
    const auto completion = read_l1(session.device, source, completion_address, sizeof(std::uint32_t));
    std::uint32_t completion_value = 0;
    std::memcpy(&completion_value, completion.data(), sizeof(completion_value));
    if (observed != expected || completion_value != kCompletionNoc) throw std::runtime_error("NoC functional check failed");
    return observed;
}

void write_dram(IDevice* device, std::uint32_t bank, std::uint32_t address, const std::vector<std::uint8_t>& bytes) {
    if (bytes.size() % sizeof(std::uint32_t) != 0) throw std::runtime_error("DRAM transfer must be word aligned");
    std::vector<std::uint32_t> words(bytes.size() / sizeof(std::uint32_t));
    std::memcpy(words.data(), bytes.data(), bytes.size());
    if (!detail::WriteToDeviceDRAMChannel(device, bank, address, words)) throw std::runtime_error("DRAM write failed");
}

std::vector<std::uint8_t> execute_dram(DeviceSession& session, const CampaignContext& context) {
    const auto coordinate = core_field(context.input, "destination_core");
    const CoreCoord core{coordinate[0], coordinate[1]};
    const std::uint32_t bank = unsigned_field(context.input, "dram_bank");
    if (bank >= session.device->allocator()->get_num_banks(BufferType::DRAM)) throw std::runtime_error("DRAM bank is unavailable");
    const std::uint32_t dram_address = session.device->allocator()->get_base_allocator_addr(HalMemType::DRAM) +
                                       unsigned_field(context.input, "address");
    const std::uint32_t l1_base = session.device->allocator()->get_base_allocator_addr(HalMemType::L1);
    const std::uint32_t destination_address = l1_base;
    const std::uint32_t completion_address = l1_base + kMaximumTransferBytes;
    const std::uint32_t bytes = unsigned_field(context.input, "bytes");
    require_l1_range(session.device, destination_address, bytes);
    require_l1_range(session.device, completion_address, sizeof(std::uint32_t));
    const auto expected = pattern(bytes, unsigned_field(context.input, "pattern_seed"),
                                  unsigned_field(context.input, "pattern_stride"));
    write_dram(session.device, bank, dram_address, expected);
    write_l1(session.device, core, destination_address, std::vector<std::uint8_t>(bytes));
    write_l1(session.device, core, completion_address, std::vector<std::uint8_t>(sizeof(std::uint32_t)));

    Program program = CreateProgram();
    const auto kernel = CreateKernel(
        program, kernel_path("dram_read_return.cpp").string(), core,
        DataMovementConfig{.processor = DataMovementProcessor::RISCV_0, .noc = NOC::RISCV_0_default});
    SetRuntimeArgs(program, kernel, core,
                   {dram_address, bank, destination_address, completion_address, bytes,
                    unsigned_field(context.input, "count")});
    detail::LaunchProgram(session.device, program);
    const auto observed = read_l1(session.device, core, destination_address, bytes);
    const auto completion = read_l1(session.device, core, completion_address, sizeof(std::uint32_t));
    std::uint32_t completion_value = 0;
    std::memcpy(&completion_value, completion.data(), sizeof(completion_value));
    if (observed != expected || completion_value != kCompletionDram) throw std::runtime_error("DRAM functional check failed");
    return observed;
}

std::vector<std::uint8_t> execute_compute(DeviceSession& session, const CampaignContext& context) {
    constexpr std::uint32_t dimension = 32;
    constexpr std::uint32_t elements = dimension * dimension;
    constexpr std::uint32_t tile_bytes = elements * sizeof(bfloat16);
    const auto coordinate = core_field(context.input, "worker_core");
    const CoreCoord core{coordinate[0], coordinate[1]};
    std::vector<bfloat16> input_a(elements, bfloat16(0.0f));
    std::vector<bfloat16> input_b(elements);
    for (std::uint32_t index = 0; index < dimension; ++index) input_a[index * dimension + index] = bfloat16(1.0f);
    const std::uint32_t seed = unsigned_field(context.input, "sentinel_seed");
    for (std::uint32_t index = 0; index < elements; ++index) input_b[index] = bfloat16(static_cast<float>(seed + index));
    const std::vector<bfloat16> expected = input_b;
    input_a = tilize_nfaces(input_a, dimension, dimension);
    input_b = tilize_nfaces(input_b, dimension, dimension);

    dist::DeviceLocalBufferConfig dram_config{.page_size = tile_bytes, .buffer_type = BufferType::DRAM};
    dist::ReplicatedBufferConfig replicated{.size = tile_bytes};
    auto source_a = dist::MeshBuffer::create(replicated, dram_config, session.mesh.get());
    auto source_b = dist::MeshBuffer::create(replicated, dram_config, session.mesh.get());
    Program program = CreateProgram();
    const auto cb_format = DataFormat::Float16_b;
    CreateCircularBuffer(program, core,
                         CircularBufferConfig(2 * tile_bytes, {{CBIndex::c_0, cb_format}}).set_page_size(CBIndex::c_0, tile_bytes));
    CreateCircularBuffer(program, core,
                         CircularBufferConfig(2 * tile_bytes, {{CBIndex::c_1, cb_format}}).set_page_size(CBIndex::c_1, tile_bytes));
    CreateCircularBuffer(program, core,
                         CircularBufferConfig(2 * tile_bytes, {{CBIndex::c_16, cb_format}}).set_page_size(CBIndex::c_16, tile_bytes));
    std::vector<std::uint32_t> reader_compile_args;
    TensorAccessorArgs(*source_a).append_to(reader_compile_args);
    TensorAccessorArgs(*source_b).append_to(reader_compile_args);
    const auto reader = CreateKernel(
        program, kernel_path("compute_reader.cpp").string(), core,
        DataMovementConfig{.processor = DataMovementProcessor::RISCV_1, .noc = NOC::RISCV_1_default,
                           .compile_args = reader_compile_args});
    const auto writer = CreateKernel(
        program, kernel_path("compute_writer.cpp").string(), core,
        DataMovementConfig{.processor = DataMovementProcessor::RISCV_0, .noc = NOC::RISCV_0_default});
    CreateKernel(program, kernel_path("compute_service.cpp").string(), core,
                 ComputeConfig{.math_fidelity = MathFidelity::HiFi2, .compile_args = {1, 1, 1}});
    const std::uint32_t output_address = session.device->allocator()->get_base_allocator_addr(HalMemType::L1) +
                                         unsigned_field(context.input, "output_address");
    require_l1_range(session.device, output_address, tile_bytes);
    SetRuntimeArgs(program, reader, core,
                   {static_cast<std::uint32_t>(source_a->address()),
                    static_cast<std::uint32_t>(source_b->address())});
    SetRuntimeArgs(program, writer, core, {output_address, tile_bytes});
    dist::EnqueueWriteMeshBuffer(session.mesh->mesh_command_queue(), source_a, input_a, false);
    dist::EnqueueWriteMeshBuffer(session.mesh->mesh_command_queue(), source_b, input_b, true);
    dist::MeshWorkload mesh_workload;
    mesh_workload.add_program(dist::MeshCoordinateRange(session.mesh->shape()), std::move(program));
    dist::EnqueueMeshWorkload(session.mesh->mesh_command_queue(), mesh_workload, true);

    auto raw = read_l1(session.device, core, output_address, tile_bytes);
    std::vector<bfloat16> output(elements);
    std::memcpy(output.data(), raw.data(), raw.size());
    output = untilize_nfaces(output, dimension, dimension);
    if (output != expected) throw std::runtime_error("compute functional check failed");
    std::vector<std::uint8_t> sentinel(unsigned_field(context.input, "sentinel_elements") * sizeof(std::uint16_t));
    for (std::size_t index = 0; index < sentinel.size() / 2; ++index) {
        const std::uint16_t bits = fp32_to_bf16_bits_round_to_nearest_even(static_cast<float>(seed + index));
        sentinel[2 * index] = bits & 0xff;
        sentinel[2 * index + 1] = bits >> 8;
    }
    return sentinel;
}

std::string hex_bytes(std::span<const std::uint8_t> bytes) {
    constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(bytes.size() * 2);
    for (const auto byte : bytes) {
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 0xf]);
    }
    return result;
}

json counter(std::string name, std::uint32_t value, std::string unit, std::string scope) {
    return {{"name", std::move(name)}, {"value", value}, {"unit", std::move(unit)}, {"scope", std::move(scope)}};
}

json entities(const CampaignContext& context) {
    const auto& boundary = context.case_record.at("boundary_maps").at(0).at("simulator_interval");
    const std::string family = context.case_record.at("family");
    if (family == "noc_ack_roundtrip") {
        const auto fabric = unsigned_field(context.mapping, "fabric_id");
        return json::array({
            {{"entity_id", "operation"}, {"role", "transfer"}, {"simulator_id", boundary.at("subject_id")}},
            {{"entity_id", "source"}, {"role", "endpoint"}, {"simulator_id", context.mapping.at("source")}, {"fabric_id", fabric}},
            {{"entity_id", "destination"}, {"role", "endpoint"}, {"simulator_id", context.mapping.at("destination")}, {"fabric_id", fabric}},
            {{"entity_id", "destination-l1"}, {"role", "resource"},
             {"simulator_id", "resource:" + context.mapping.at("destination").get<std::string>() + ":l1"},
             {"physical_owner", "destination"}, {"fabric_id", fabric}},
        });
    }
    if (family == "dram_read_return") {
        return json::array({
            {{"entity_id", "operation"}, {"role", "transfer"}, {"simulator_id", boundary.at("subject_id")}},
            {{"entity_id", "destination"}, {"role", "worker"}, {"simulator_id", context.mapping.at("destination")}},
            {{"entity_id", "dram"}, {"role", "resource"}, {"simulator_id", boundary.at("resource_id")}},
        });
    }
    return json::array({
        {{"entity_id", "operation"}, {"role", "job"}, {"simulator_id", boundary.at("subject_id")}},
        {{"entity_id", "worker"}, {"role", "worker"}, {"simulator_id", context.mapping.at("worker")}},
        {{"entity_id", "compute-resource"}, {"role", "resource"}, {"simulator_id", boundary.at("resource_id")}, {"physical_owner", "worker"}},
        {{"entity_id", "output-l1"}, {"role", "resource"}, {"simulator_id", context.mapping.at("output_resource")}, {"physical_owner", "worker"}},
    });
}

json repetition_record(const CampaignContext& context, const std::string& repetition_id,
                       std::span<const std::uint8_t> sentinel) {
    const std::string family = context.case_record.at("family");
    const std::string case_id = context.case_record.at("case_id");
    const auto& interval = context.case_record.at("boundary_maps").at(0).at("simulator_interval");
    std::vector<std::string> actions;
    std::map<std::size_t, std::string> mapped;
    json counters = json::array();
    json effect;
    std::size_t visibility = 1;
    if (family == "noc_ack_roundtrip") {
        actions = {"submission", "remote_write_visible", "acknowledged_completion"};
        mapped = {{0, interval.at("start_event_id")}, {2, interval.at("end_event_id")}};
        counters = json::array({json::array({counter("bytes", unsigned_field(context.workload, "bytes"), "bytes", "planned")}),
                                json::array({counter("bytes", unsigned_field(context.workload, "bytes"), "bytes", "observed")}),
                                json::array({counter("acknowledgements", 1, "count", "observed")})});
        effect = {{"destination_id", "destination"}, {"resource_id", "destination-l1"},
                  {"offset_bytes", context.workload.at("destination_address")}, {"size_bytes", context.workload.at("bytes")},
                  {"count", context.workload.at("count")}};
    } else if (family == "dram_read_return") {
        actions = {"submission", "memory_service_begin", "memory_service_end", "worker_visible_completion"};
        mapped = {{1, interval.at("start_event_id")}, {2, interval.at("end_event_id")}};
        counters = json::array({json::array({counter("bytes", unsigned_field(context.workload, "bytes"), "bytes", "planned")}),
                                json::array(), json::array(),
                                json::array({counter("bytes", unsigned_field(context.workload, "bytes"), "bytes", "observed")})});
        effect = {{"destination_id", "destination"}, {"resource_id", "dram"},
                  {"offset_bytes", context.workload.at("address")}, {"size_bytes", context.workload.at("bytes")},
                  {"count", context.workload.at("count")}};
        visibility = 3;
    } else {
        actions = {"compute_resource_acquire", "result_visible", "compute_resource_release"};
        mapped = {{0, interval.at("start_event_id")}, {2, interval.at("end_event_id")}};
        counters = json::array({json::array({counter("work", unsigned_field(context.workload, "work"), "work", "planned")}),
                                json::array({counter("bytes", unsigned_field(context.workload, "output_bytes"), "bytes", "observed")}),
                                json::array({counter("work", unsigned_field(context.workload, "work"), "work", "observed")})});
        effect = {{"destination_id", "worker"}, {"resource_id", "output-l1"},
                  {"offset_bytes", context.workload.at("output_address")}, {"size_bytes", context.workload.at("output_bytes")},
                  {"count", 1}};
    }
    const std::string prefix = "repetition:" + repetition_id + ":";
    json events = json::array();
    for (std::size_t index = 0; index < actions.size(); ++index) {
        const std::string simulator = mapped.contains(index) ? mapped.at(index) : "external:" + case_id + ":" + actions[index];
        events.push_back({{"event_id", prefix + actions[index]}, {"action", actions[index]}, {"subject_id", "operation"},
                          {"sequence", index}, {"simulator_event_id", prefix + simulator}, {"counters", counters.at(index)}});
    }
    effect["effect_id"] = prefix + "effect";
    effect["visibility_event"] = events.at(visibility).at("event_id");
    effect["simulator_effect_id"] = prefix + "effect:" + case_id;
    return {{"repetition_id", repetition_id}, {"status", "pass"}, {"completion_marker", kCompletionMarker},
            {"sentinel_algorithm", "sha256"}, {"sentinel_payload_hex", hex_bytes(sentinel)},
            {"sentinel_sha256", wormhole_external::sha256(sentinel)}, {"events", events},
            {"effects", json::array({effect})}};
}

void write_new_file(const std::filesystem::path& path, std::span<const std::uint8_t> bytes) {
    if (std::filesystem::exists(path)) throw std::runtime_error("producer refuses to overwrite output: " + path.string());
    std::filesystem::create_directories(path.parent_path());
    const auto temporary = path.string() + ".partial";
    if (std::filesystem::exists(temporary)) throw std::runtime_error("stale partial output exists: " + temporary);
    {
        std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
        if (!stream) throw std::runtime_error("cannot create output: " + temporary);
        stream.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
        if (!stream) throw std::runtime_error("cannot write output: " + temporary);
    }
    std::filesystem::rename(temporary, path);
}

std::vector<std::uint8_t> serialized(const json& value) {
    const std::string text = value.dump(2) + "\n";
    return {text.begin(), text.end()};
}

int run(const Invocation& invocation) {
    CampaignContext context = load_context(invocation);
    const std::string runtime_root = std::filesystem::absolute(std::getenv("TT_METAL_HOME")).string();
    if (::setenv("TT_METAL_RUNTIME_ROOT", runtime_root.c_str(), 1) != 0) {
        throw std::runtime_error("cannot configure the pinned TT-Metal runtime root");
    }
    DeviceSession session;
    json repetitions = json::array();
    const auto& repetition_ids = context.case_record.at("boundary_maps").at(0).at("samples").at("repetition_ids");
    for (std::uint32_t index = 0; index < invocation.repetitions; ++index) {
        std::vector<std::uint8_t> sentinel;
        if (invocation.recipe == "noc_ack_roundtrip_v1") sentinel = execute_noc(session, context);
        else if (invocation.recipe == "dram_read_return_v1") sentinel = execute_dram(session, context);
        else sentinel = execute_compute(session, context);
        repetitions.push_back(repetition_record(context, repetition_ids.at(index).get<std::string>(), sentinel));
    }
    if (!session.mesh->close()) throw std::runtime_error("device did not close cleanly");
    session.mesh.reset();
    json record = {
        {"kind", "tt_metal_functional_record"}, {"schema_version", 1},
        {"case_id", context.case_record.at("case_id")}, {"case_family", context.case_record.at("family")},
        {"producer_id", context.producer.at("producer_id")}, {"adapter", context.producer.at("adapter")},
        {"build_id", context.build.at("build_id")}, {"input_artifact", context.case_record.at("simulator_input")},
        {"binary_artifacts", context.build.at("artifacts")},
        {"workload", context.case_record.at("conditions").at("workload").at("value")},
        {"mapping", context.case_record.at("conditions").at("mapping").at("value")},
        {"enabled_layout", context.case_record.at("conditions").at("enabled_layout").at("value")},
        {"instrumentation", context.case_record.at("conditions").at("instrumentation").at("value")},
        {"entities", entities(context)}, {"repetitions", repetitions},
    };
    const auto functional_bytes = serialized(record);
    json manifest = {
        {"kind", "tt_metal_capture_manifest"}, {"schema_version", 1}, {"status", "pass"},
        {"completion_marker", kCompletionMarker}, {"case_id", context.case_record.at("case_id")},
        {"recipe", invocation.recipe}, {"input_sha256", context.case_record.at("simulator_input").at("sha256")},
        {"functional_sha256", wormhole_external::sha256(functional_bytes)},
        {"repetitions", invocation.repetitions}, {"warmup_repetitions", invocation.warmups},
        {"timeout_seconds", invocation.timeout_seconds}, {"max_output_bytes", invocation.max_output_bytes},
    };
    const auto manifest_bytes = serialized(manifest);
    if (functional_bytes.size() + manifest_bytes.size() > invocation.max_output_bytes) {
        throw std::runtime_error("producer outputs exceed the declared byte budget");
    }
    write_new_file(invocation.functional_output, functional_bytes);
    try {
        write_new_file(invocation.manifest_output, manifest_bytes);
    } catch (...) {
        std::filesystem::remove(invocation.functional_output);
        throw;
    }
    return 0;
}
}  // namespace

int main(int argc, char** argv) {
    try {
        const Invocation invocation = parse_invocation(argc, argv);
        require_environment();
        return run(invocation);
    } catch (const std::exception& error) {
        std::fprintf(stderr, "wormhole external validation failed: %s\n", error.what());
        return 2;
    }
}
