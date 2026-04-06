/*
 * pw_capture — PipeWire capture via portal FD.
 * Enumerates nodes on the portal remote, captures frames, writes to stdout.
 * Usage: pw_capture <pw_fd> <node_id> [width] [height]
 */

#include <pipewire/pipewire.h>
#include <spa/param/video/format-utils.h>
#include <spa/param/video/type-info.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>

struct data {
    struct pw_main_loop *loop;
    struct pw_core *core;
    struct pw_registry *registry;
    struct spa_hook registry_listener;
    struct pw_stream *stream;
    struct spa_hook stream_listener;
    struct spa_video_info format;
    int width, height;
    uint64_t frame_count;
    int target_node;
    int found_node;
};

/* ── Registry: list all objects on the portal remote ── */

static void registry_global(void *udata, uint32_t id, uint32_t perms,
                            const char *type, uint32_t version,
                            const struct spa_dict *props)
{
    struct data *d = udata;
    const char *name = props ? spa_dict_lookup(props, PW_KEY_NODE_NAME) : NULL;
    const char *media = props ? spa_dict_lookup(props, PW_KEY_MEDIA_CLASS) : NULL;
    fprintf(stderr, "[registry] id=%u type=%s name=%s media=%s\n",
            id, type,
            name ? name : "(null)",
            media ? media : "(null)");

    /* Track if we find any node */
    if (type && strcmp(type, PW_TYPE_INTERFACE_Node) == 0) {
        d->found_node = id;
        fprintf(stderr, "[registry] *** FOUND NODE id=%u ***\n", id);
    }
}

static const struct pw_registry_events registry_events = {
    PW_VERSION_REGISTRY_EVENTS,
    .global = registry_global,
};

/* ── Stream callbacks ── */

static void on_process(void *udata)
{
    struct data *d = udata;
    struct pw_buffer *buf = pw_stream_dequeue_buffer(d->stream);
    if (!buf) return;

    struct spa_buffer *sbuf = buf->buffer;
    void *ptr = sbuf->datas[0].data;
    size_t size = sbuf->datas[0].chunk->size;

    if (ptr && size > 0) {
        write(STDOUT_FILENO, ptr, size);
        d->frame_count++;
        if (d->frame_count <= 3 || d->frame_count % 300 == 0) {
            fprintf(stderr, "[capture] frame #%lu: %zu bytes (%dx%d)\n",
                    d->frame_count, size, d->width, d->height);
        }
    }

    pw_stream_queue_buffer(d->stream, buf);
}

static void on_param_changed(void *udata, uint32_t id, const struct spa_pod *param)
{
    struct data *d = udata;
    if (!param || id != SPA_PARAM_Format) return;
    if (spa_format_video_raw_parse(param, &d->format.info.raw) < 0) return;
    d->width = d->format.info.raw.size.width;
    d->height = d->format.info.raw.size.height;
    fprintf(stderr, "[capture] Format: %dx%d format=%d\n",
            d->width, d->height, d->format.info.raw.format);
}

static void on_state_changed(void *udata, enum pw_stream_state old,
                             enum pw_stream_state state, const char *error)
{
    fprintf(stderr, "[stream] %s → %s%s%s\n",
            pw_stream_state_as_string(old),
            pw_stream_state_as_string(state),
            error ? " error=" : "",
            error ? error : "");
}

static const struct pw_stream_events stream_events = {
    PW_VERSION_STREAM_EVENTS,
    .state_changed = on_state_changed,
    .param_changed = on_param_changed,
    .process = on_process,
};

/* ── Main ── */

int main(int argc, char *argv[])
{
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <pw_fd> <node_id> [width] [height]\n", argv[0]);
        return 1;
    }

    int pw_fd = atoi(argv[1]);
    int node_id = atoi(argv[2]);
    int req_w = argc > 3 ? atoi(argv[3]) : 1280;
    int req_h = argc > 4 ? atoi(argv[4]) : 800;

    signal(SIGPIPE, SIG_IGN);
    pw_init(NULL, NULL);

    struct data d = {0};
    d.target_node = node_id;
    d.loop = pw_main_loop_new(NULL);
    struct pw_context *ctx = pw_context_new(
        pw_main_loop_get_loop(d.loop), NULL, 0);

    /* Try connecting to the DEFAULT PipeWire instance (not the portal FD).
     * The portal session being active should authorize access to node. */
    fprintf(stderr, "[main] Connecting to default PipeWire (ignoring portal FD %d)...\n", pw_fd);
    d.core = pw_context_connect(ctx, NULL, 0);
    if (!d.core) {
        fprintf(stderr, "[main] Default connect failed, trying FD %d...\n", pw_fd);
        d.core = pw_context_connect_fd(ctx, pw_fd, NULL, 0);
    }
    if (!d.core) {
        fprintf(stderr, "[main] FAILED to connect\n");
        return 1;
    }
    fprintf(stderr, "[main] Connected!\n");

    /* Get registry to enumerate objects */
    d.registry = pw_core_get_registry(d.core, PW_VERSION_REGISTRY, 0);
    pw_registry_add_listener(d.registry, &d.registry_listener,
                             &registry_events, &d);

    /* Sync: after registry is done, we'll have all objects listed */
    /* For now, just create the stream directly */

    /* On the portal remote, there are no target nodes directly visible.
     * The portal auto-routes our stream to the screencast source.
     * Don't set any target — let the portal handle routing. */
    struct pw_properties *props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Video",
        PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Screen",
        NULL);

    d.stream = pw_stream_new(d.core, "prysm-capture", props);
    pw_stream_add_listener(d.stream, &d.stream_listener,
                           &stream_events, &d);

    struct spa_rectangle size = SPA_RECTANGLE(req_w, req_h);
    struct spa_fraction framerate = SPA_FRACTION(0, 1);

    uint8_t buffer[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));
    const struct spa_pod *params[1];
    params[0] = spa_pod_builder_add_object(&b,
        SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat,
        SPA_FORMAT_mediaType,    SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        SPA_FORMAT_VIDEO_format, SPA_POD_CHOICE_ENUM_Id(3,
            SPA_VIDEO_FORMAT_BGRx,
            SPA_VIDEO_FORMAT_BGRx,
            SPA_VIDEO_FORMAT_NV12),
        SPA_FORMAT_VIDEO_size,   SPA_POD_Rectangle(&size),
        SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&framerate));

    fprintf(stderr, "[main] Connecting stream (target=%d)...\n", node_id);
    pw_stream_connect(d.stream,
        PW_DIRECTION_INPUT,
        PW_ID_ANY,
        PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS,
        params, 1);

    fprintf(stderr, "[main] Running main loop...\n");
    pw_main_loop_run(d.loop);

    fprintf(stderr, "[main] Done. %lu frames.\n", d.frame_count);
    pw_stream_destroy(d.stream);
    pw_core_disconnect(d.core);
    pw_context_destroy(ctx);
    pw_main_loop_destroy(d.loop);
    pw_deinit();
    return 0;
}
