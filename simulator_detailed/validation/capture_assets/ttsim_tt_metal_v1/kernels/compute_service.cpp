// SPDX-License-Identifier: Apache-2.0
#include "api/compute/compute_kernel_hw_startup.h"
#include "api/compute/matmul.h"
#include "api/compute/tile_move_copy.h"

#include <cstdint>

namespace {
constexpr std::uint32_t kMaximumTilesPerAxis = 32;
}  // namespace

void kernel_main() {
    const std::uint32_t rows = get_compile_time_arg_val(0);
    const std::uint32_t inner = get_compile_time_arg_val(1);
    const std::uint32_t columns = get_compile_time_arg_val(2);
    constexpr tt::CBIndex input_a = tt::CBIndex::c_0;
    constexpr tt::CBIndex input_b = tt::CBIndex::c_1;
    constexpr tt::CBIndex output = tt::CBIndex::c_16;

    if (rows == 0 || inner == 0 || columns == 0 || rows > kMaximumTilesPerAxis ||
        inner > kMaximumTilesPerAxis || columns > kMaximumTilesPerAxis) {
        return;
    }
    DeviceZoneScopedN("COMPUTE_SERVICE");
    compute_kernel_hw_startup<SrcOrder::Reverse>(input_a, input_b, output);
    matmul_init(input_a, input_b);
    for (std::uint32_t row = 0; row < rows; ++row) {
        for (std::uint32_t column = 0; column < columns; ++column) {
            tile_regs_acquire();
            for (std::uint32_t k = 0; k < inner; ++k) {
                cb_wait_front(input_a, 1);
                cb_wait_front(input_b, 1);
                matmul_tiles(input_a, input_b, 0, 0, 0);
                cb_pop_front(input_a, 1);
                cb_pop_front(input_b, 1);
            }
            tile_regs_commit();
            tile_regs_wait();
            cb_reserve_back(output, 1);
            pack_tile(0, output);
            cb_push_back(output, 1);
            tile_regs_release();
        }
    }
}
