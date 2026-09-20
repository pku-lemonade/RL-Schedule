// SPDX-License-Identifier: Apache-2.0
#include "api/dataflow/dataflow_api.h"

#include <cstdint>

namespace {
constexpr std::uint32_t kMaximumBytes = 4096;
constexpr std::uint32_t kMaximumCount = 64;
constexpr std::uint32_t kCompletionValue = 0x57484f4c;
}  // namespace

void kernel_main() {
    const std::uint32_t destination_x = get_arg_val<std::uint32_t>(0);
    const std::uint32_t destination_y = get_arg_val<std::uint32_t>(1);
    const std::uint32_t source_l1_address = get_arg_val<std::uint32_t>(2);
    const std::uint32_t destination_l1_address = get_arg_val<std::uint32_t>(3);
    const std::uint32_t acknowledgement_l1_address = get_arg_val<std::uint32_t>(4);
    const std::uint32_t byte_count = get_arg_val<std::uint32_t>(5);
    const std::uint32_t count = get_arg_val<std::uint32_t>(6);

    if (byte_count == 0 || byte_count > kMaximumBytes || count == 0 || count > kMaximumCount) {
        return;
    }
    DeviceZoneScopedN("NOC_ACK_ROUNDTRIP");
    const std::uint64_t destination =
        get_noc_addr(destination_x, destination_y, destination_l1_address);
    for (std::uint32_t index = 0; index < count; ++index) {
        noc_async_write(source_l1_address, destination, byte_count);
        noc_async_write_barrier();
    }

    auto* acknowledgement =
        reinterpret_cast<volatile tt_l1_ptr std::uint32_t*>(acknowledgement_l1_address);
    acknowledgement[0] = kCompletionValue;
    noc_async_writes_flushed();
}
