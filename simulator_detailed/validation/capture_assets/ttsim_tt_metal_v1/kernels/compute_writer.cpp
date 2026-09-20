// SPDX-License-Identifier: Apache-2.0
#include "api/dataflow/dataflow_api.h"

void kernel_main() {
    const std::uint32_t output_address = get_arg_val<std::uint32_t>(0);
    const std::uint32_t output_bytes = get_arg_val<std::uint32_t>(1);
    constexpr std::uint32_t output = 16;

    cb_wait_front(output, 1);
    noc_async_write(get_read_ptr(output), get_noc_addr(output_address), output_bytes);
    noc_async_write_barrier();
    cb_pop_front(output, 1);
}
