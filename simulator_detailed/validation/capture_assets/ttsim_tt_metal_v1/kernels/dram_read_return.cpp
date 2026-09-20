// SPDX-License-Identifier: Apache-2.0
#include "api/dataflow/dataflow_api.h"

#include <cstdint>

namespace {
constexpr std::uint32_t kMaximumBytes = 4096;
constexpr std::uint32_t kCompletionValue = 0x4452414d;
}  // namespace

void kernel_main() {
    const std::uint32_t dram_address = get_arg_val<std::uint32_t>(0);
    const std::uint32_t destination_l1_address = get_arg_val<std::uint32_t>(1);
    const std::uint32_t completion_l1_address = get_arg_val<std::uint32_t>(2);
    const std::uint32_t byte_count = get_arg_val<std::uint32_t>(3);
    constexpr auto dram_accessor_args = TensorAccessorArgs<0>();

    if (byte_count == 0 || byte_count > kMaximumBytes) {
        return;
    }
    {
        DeviceZoneScopedN("DRAM_READ_RETURN");
        const auto dram = TensorAccessor(dram_accessor_args, dram_address, byte_count);
        noc_async_read_page(0, dram, destination_l1_address);
        noc_async_read_barrier();
    }

    auto* completion = reinterpret_cast<volatile tt_l1_ptr std::uint32_t*>(completion_l1_address);
    completion[0] = kCompletionValue;
    noc_async_writes_flushed();
}
