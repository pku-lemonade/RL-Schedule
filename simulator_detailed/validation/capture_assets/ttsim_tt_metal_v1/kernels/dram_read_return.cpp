// SPDX-License-Identifier: Apache-2.0
#include "api/dataflow/dataflow_api.h"

#include <cstdint>

namespace {
constexpr std::uint32_t kMaximumBytes = 4096;
constexpr std::uint32_t kMaximumCount = 64;
constexpr std::uint32_t kCompletionValue = 0x4452414d;
}  // namespace

void kernel_main() {
    const std::uint32_t dram_address = get_arg_val<std::uint32_t>(0);
    const std::uint32_t dram_bank = get_arg_val<std::uint32_t>(1);
    const std::uint32_t destination_l1_address = get_arg_val<std::uint32_t>(2);
    const std::uint32_t completion_l1_address = get_arg_val<std::uint32_t>(3);
    const std::uint32_t byte_count = get_arg_val<std::uint32_t>(4);
    const std::uint32_t count = get_arg_val<std::uint32_t>(5);

    if (byte_count == 0 || byte_count > kMaximumBytes || count == 0 || count > kMaximumCount) {
        return;
    }
    {
        DeviceZoneScopedN("DRAM_READ_RETURN");
        const std::uint64_t dram = get_noc_addr_from_bank_id<true>(dram_bank, dram_address);
        for (std::uint32_t index = 0; index < count; ++index) {
            noc_async_read(dram, destination_l1_address, byte_count);
            noc_async_read_barrier();
        }
    }

    auto* completion = reinterpret_cast<volatile tt_l1_ptr std::uint32_t*>(completion_l1_address);
    completion[0] = kCompletionValue;
    noc_async_writes_flushed();
}
