(function() {
    var type_impls = Object.fromEntries([["rtcp",[]],["serde",[]],["serde_core",[]],["webrtc",[]],["webrtc_dtls",[]]]);
    if (window.register_type_impls) {
        window.register_type_impls(type_impls);
    } else {
        window.pending_type_impls = type_impls;
    }
})()
//{"start":55,"fragment_lengths":[11,13,18,14,19]}