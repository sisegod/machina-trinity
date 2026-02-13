#ifdef MACHINA_USE_CUDA
#include <cuda_runtime.h>
#include <cstdint>

// Simple batched dot product kernel.
__global__ void dot_kernel(const float* goal, const float* centroids, int n, int dim, float* out_scores) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    float s = 0.0f;
    const float* row = centroids + (size_t)i * (size_t)dim;
    for (int d = 0; d < dim; d++) s += goal[d] * row[d];
    out_scores[i] = s;
}

static bool ensure_capacity(float** dptr, size_t* cap_bytes, size_t need_bytes) {
    if (need_bytes == 0) return true;
    if (*dptr != nullptr && *cap_bytes >= need_bytes) return true;
    if (*dptr != nullptr) cudaFree(*dptr);
    cudaError_t err = cudaMalloc((void**)dptr, need_bytes);
    if (err != cudaSuccess) {
        *dptr = nullptr;
        *cap_bytes = 0;
        return false;
    }
    *cap_bytes = need_bytes;
    return true;
}

extern "C" void machina_cuda_batched_dot(const float* goal, const float* centroids, int n, int dim, float* out_scores) {
    if (!goal || !centroids || !out_scores || n <= 0 || dim <= 0) return;

    // Crunch9: reuse allocations + single stream to avoid cudaMalloc/cudaFree per call.
    static cudaStream_t stream = nullptr;
    static bool stream_inited = false;

    static float* d_goal = nullptr;
    static float* d_centroids = nullptr;
    static float* d_scores = nullptr;
    static size_t cap_goal = 0;
    static size_t cap_centroids = 0;
    static size_t cap_scores = 0;

    if (!stream_inited) {
        if (cudaStreamCreateWithFlags(&stream, cudaStreamNonBlocking) != cudaSuccess) {
            stream = nullptr;
        }
        stream_inited = true;
    }

    size_t goal_bytes = (size_t)dim * sizeof(float);
    size_t cent_bytes = (size_t)n * (size_t)dim * sizeof(float);
    size_t score_bytes = (size_t)n * sizeof(float);

    if (!ensure_capacity(&d_goal, &cap_goal, goal_bytes)) return;
    if (!ensure_capacity(&d_centroids, &cap_centroids, cent_bytes)) return;
    if (!ensure_capacity(&d_scores, &cap_scores, score_bytes)) return;

    // Copy inputs
    if (stream) {
        cudaMemcpyAsync(d_goal, goal, goal_bytes, cudaMemcpyHostToDevice, stream);
        cudaMemcpyAsync(d_centroids, centroids, cent_bytes, cudaMemcpyHostToDevice, stream);
    } else {
        cudaMemcpy(d_goal, goal, goal_bytes, cudaMemcpyHostToDevice);
        cudaMemcpy(d_centroids, centroids, cent_bytes, cudaMemcpyHostToDevice);
    }

    int threads = 128;
    int blocks = (n + threads - 1) / threads;
    if (stream) {
        dot_kernel<<<blocks, threads, 0, stream>>>(d_goal, d_centroids, n, dim, d_scores);
        cudaMemcpyAsync(out_scores, d_scores, score_bytes, cudaMemcpyDeviceToHost, stream);
        cudaStreamSynchronize(stream);
    } else {
        dot_kernel<<<blocks, threads>>>(d_goal, d_centroids, n, dim, d_scores);
        cudaDeviceSynchronize();
        cudaMemcpy(out_scores, d_scores, score_bytes, cudaMemcpyDeviceToHost);
    }
}
#endif
