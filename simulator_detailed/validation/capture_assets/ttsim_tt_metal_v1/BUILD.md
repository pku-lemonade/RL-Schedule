# Pinned ttsim worker build

The capture campaign pins the TT-Metal and ttsim revisions in its build and
runtime manifests. From clean checkouts at those revisions:

1. Initialize the TT-Metal submodules at their recorded commits.
2. Copy this `producer` directory to `validation-producer` at the TT-Metal
   checkout root.
3. Apply `validation-producer/patches/tt-metal-ttsim-single-rank.patch` from
   the TT-Metal checkout root. The patch adds the producer target and
   implements the one-rank identity result required by TT-Metal control-plane
   validation.
4. Configure a Wormhole release build with device-profiler support and build
   the `wormhole_external_validation` target:

   ```sh
   cmake -S . -B build-validation -G Ninja \
     -DCMAKE_BUILD_TYPE=Release \
     -DENABLE_TRACY=ON \
     -DTT_UMD_BUILD_SIMULATION=ON \
     -DTT_METAL_BUILD_TESTS=OFF \
     -DWITH_PYTHON_BINDINGS=OFF
   cmake --build build-validation --target wormhole_external_validation
   ```

   `ENABLE_TRACY=ON` is required by `TT_METAL_DEVICE_PROFILER=1`; a
   profiler-disabled Metalium runtime is not an admissible silicon producer.
5. Place the host executable, the built Wormhole ttsim library, and every
   runtime library at the logical paths and hashes declared by the campaign
   before collection. The fixed invocation sets `LD_LIBRARY_PATH=bin/runtime`;
   the profiler-enabled evidence build declares `libtt_metal`, Tracy, UMD,
   TT-STL and hwloc in that directory instead of relying on build-tree paths.

The patch changes only the single-rank ttsim control-plane path. The same
profile-enabled binary uses the pinned upstream device path when
`TT_METAL_SIMULATOR` is absent.
