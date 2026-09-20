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
4. Configure a Wormhole release build and build the
   `wormhole_external_validation` target.
5. Place the host executable, `libtt_metal.so`, and the built Wormhole ttsim
   library at the logical paths and hashes declared by the campaign before
   collection.

The patch is specific to the single-rank ttsim worker. It is not applied to a
silicon collector build.
