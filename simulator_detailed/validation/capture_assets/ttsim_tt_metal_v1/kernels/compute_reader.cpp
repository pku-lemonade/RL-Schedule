// SPDX-License-Identifier: Apache-2.0
#include "api/dataflow/dataflow_api.h"

void kernel_main() {
    const std::uint32_t source_a = get_arg_val<std::uint32_t>(0);
    const std::uint32_t source_b = get_arg_val<std::uint32_t>(1);
    constexpr std::uint32_t input_a = 0;
    constexpr std::uint32_t input_b = 1;
    constexpr auto source_a_args = TensorAccessorArgs<0>();
    constexpr auto source_b_args = TensorAccessorArgs<source_a_args.next_compile_time_args_offset()>();
    const auto accessor_a = TensorAccessor(source_a_args, source_a);
    const auto accessor_b = TensorAccessor(source_b_args, source_b);

    cb_reserve_back(input_a, 1);
    noc_async_read_page(0, accessor_a, get_write_ptr(input_a));
    noc_async_read_barrier();
    cb_push_back(input_a, 1);

    cb_reserve_back(input_b, 1);
    noc_async_read_page(0, accessor_b, get_write_ptr(input_b));
    noc_async_read_barrier();
    cb_push_back(input_b, 1);
}
