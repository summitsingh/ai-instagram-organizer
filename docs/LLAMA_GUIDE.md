# Llama API Guide

> Merged from `LLAMA_API_GUIDE.md` (setup, usage, troubleshooting) and
> `LLAMA_PERFORMANCE_GUIDE.md` (throughput tuning and benchmarks).

## Quick Setup

### 1. Get Your API Key

1. Visit [Llama API](https://api.llama.com)
2. Sign up for an account
3. Generate your API key from the dashboard

### 2. Configure the Tool

**Option A: Use the setup script (recommended)**

```bash
python utils/setup_llama.py
```

**Option B: Manual setup**

```bash
# Set environment variable
export LLAMA_API_KEY="your-api-key-here"

# Or update config.json directly
```

### 3. Test the Integration

```bash
# Run the test script
python tests/test_llama_api.py

# Or run a quick test with the main tool
python ai_instagram_organizer.py --dev-mode --limit 5
```

## Configuration

### API Key Lookup Order

The tool looks for the API key in this order:

1. `--llama-key` command line argument
2. `LLAMA_API_KEY` environment variable
3. `api_key` in the config.json file

### Config File Settings

```json
{
  "ai_provider": "llama",
  "llama": {
    "api_key": "your-api-key-here",
    "model": "Llama-4-Maverick-17B-128E-Instruct-FP8",
    "api_url": "https://api.llama.com/v1/chat/completions",
    "timeout": 120
  }
}
```

## Features

### Image Analysis

The Llama API provides:

- **Technical quality scoring**: evaluates image sharpness, exposure, composition
- **Visual appeal assessment**: rates aesthetic qualities and visual impact
- **Engagement prediction**: estimates potential Instagram engagement
- **Content categorization**: classifies images by type (portrait, landscape, etc.)
- **Mood detection**: identifies the emotional tone of images
- **Contextual understanding**: analyzes setting, lighting, and scene elements

### Content Generation

- **Smart captions**: generates 3 different caption options per post
- **Hashtag optimization**: creates relevant hashtags based on image content
- **Theme detection**: identifies overall post themes for consistency

## Usage Examples

### Basic Usage

```bash
# Use default Llama provider
python ai_instagram_organizer.py --source "/path/to/photos"

# Specify Llama explicitly
python ai_instagram_organizer.py --ai-provider llama --source "/path/to/photos"

# With API key override
python ai_instagram_organizer.py --llama-key "your-key" --source "/path/to/photos"
```

### Development Mode

```bash
# Test with 5 photos
python ai_instagram_organizer.py --dev-mode --limit 5

# Test with specific folder
python ai_instagram_organizer.py --dev-mode --limit 10 --source "/path/to/test/photos"
```

### High-Speed Processing

```bash
# Maximum throughput for large collections
python ai_instagram_organizer.py \
  --source "/path/to/photos" \
  --parallel-workers 50 \
  --batch-size 8 \
  --ai-provider llama
```

### Conservative Processing

```bash
# Safer settings for unstable connections
python ai_instagram_organizer.py \
  --source "/path/to/photos" \
  --parallel-workers 10 \
  --batch-size 3 \
  --ai-provider llama
```

## Performance Optimization

### Rate Limit Optimization

- **3000 requests/minute** = 50 requests/second
- **Adaptive rate limiting** with automatic throttling
- **Burst mode** for handling traffic spikes
- **Concurrent request management** (up to 50 simultaneous)

### Processing Optimizations

- **Intelligent batching** with optimal batch sizes
- **High-concurrency processing** (25-50 workers)
- **Fast timeouts** (30s vs 120s default)
- **Smart caching** to avoid re-processing

### Optimized Settings

```json
{
  "ai_provider": "llama",
  "performance": {
    "ai_analysis": {
      "parallel_workers": 25,
      "batch_size": 5,
      "max_retries": 3,
      "timeout": 60,
      "rate_limit_delay": 0.5,
      "batch_delay": 1.0
    }
  },
  "llama": {
    "performance": {
      "max_requests_per_minute": 3000,
      "max_concurrent_requests": 50,
      "adaptive_rate_limiting": true,
      "burst_mode": true,
      "optimal_batch_size": 8,
      "fast_timeout": 30
    }
  }
}
```

### Performance Profiles

#### Conservative (Safe)

Good for: small batches, testing, unstable connections

```json
{
  "parallel_workers": 10,
  "batch_size": 3,
  "rate_limit_delay": 1.0,
  "max_concurrent": 20
}
```

#### Optimized (Recommended)

Good for: most use cases, balanced speed/reliability

```json
{
  "parallel_workers": 25,
  "batch_size": 5,
  "rate_limit_delay": 0.5,
  "max_concurrent": 50
}
```

#### High Throughput (Aggressive)

Good for: large batches, stable connections, maximum speed

```json
{
  "parallel_workers": 50,
  "batch_size": 8,
  "rate_limit_delay": 0.2,
  "max_concurrent": 50
}
```

### Tuning Guidelines

#### Photography Studios (1000+ images)

```json
{
  "parallel_workers": 50,
  "batch_size": 8,
  "rate_limit_delay": 0.2,
  "enable_fast_mode": true
}
```

#### Content Creators (50-200 images)

```json
{
  "parallel_workers": 25,
  "batch_size": 5,
  "rate_limit_delay": 0.5,
  "enable_fast_mode": true
}
```

#### Casual Users (10-50 images)

```json
{
  "parallel_workers": 10,
  "batch_size": 3,
  "rate_limit_delay": 1.0,
  "enable_fast_mode": false
}
```

### Network & System Tips

- **Stable connection**: use high throughput settings
- **Unstable connection**: reduce workers and increase delays
- **Limited bandwidth**: enable fast mode for pre-filtering
- **High-end system**: 50 workers, batch size 8
- **Mid-range system**: 25 workers, batch size 5
- **Low-end system**: 10 workers, batch size 3

## Performance Benchmarks

### Throughput Comparison

| Configuration | Images/Second | 100 Images | 1000 Images |
|---------------|---------------|------------|-------------|
| **Original**  | 0.3-0.5       | 5-7 min    | 50-70 min   |
| **Optimized** | 2.5-4.0       | 30-45 sec  | 5-8 min     |
| **High Speed**| 4.0-6.0       | 20-30 sec  | 3-5 min     |

### Real-World Performance

- **Small batches** (10-50 images): 3-5x faster
- **Medium batches** (100-500 images): 5-8x faster
- **Large batches** (1000+ images): 8-12x faster

The optimized Llama API integration processes images **5-12x faster** than the
original implementation while maintaining high reliability and respecting API
limits.

### Advanced Features

- **Adaptive rate limiting**: automatic throttling based on error rates, success
  rate monitoring, burst handling, recovery optimization after rate limit hits
- **Smart batching**: dynamic batch sizing based on performance, optimal
  concurrency calculation, load balancing across workers, failure recovery with
  individual fallback
- **Intelligent caching**: result caching to avoid re-analysis, cache
  invalidation based on file changes, memory optimization for large collections,
  persistent caching across runs

## Performance Testing

### Run Performance Tests

```bash
# Test different configurations
python tests/test_llama_performance.py

# Test rate limiter functionality
python -c "from tests.test_llama_performance import test_rate_limiter; test_rate_limiter()"
```

### Monitor Performance

```bash
# Enable detailed logging
export LLAMA_DEBUG=1
python ai_instagram_organizer.py --source "/path/to/photos"
```

### Benchmark Your Setup

```bash
# Test with your specific images
python ai_instagram_organizer.py \
  --dev-mode --limit 20 \
  --source "/path/to/test/photos" \
  --ai-provider llama
```

## Best Practices

1. **Start conservative**: begin with default optimized settings
2. **Monitor success rate**: keep above 95% for best results
3. **Adjust gradually**: increase workers/batch size incrementally
4. **Use fast mode**: enable for large collections (1000+ images)
5. **Cache results**: enable caching for repeated processing
6. **Test first**: use dev mode to test settings before full runs

## Switching Between Providers

You can easily switch between AI providers:

```bash
# Use Llama (default)
python ai_instagram_organizer.py --ai-provider llama

# Use Gemini
python ai_instagram_organizer.py --ai-provider gemini --gemini-key YOUR_KEY

# Use Ollama (local)
python ai_instagram_organizer.py --ai-provider ollama
```

### Provider Comparison

| Provider | Speed | Quality | Cost | Setup |
|----------|-------|---------|------|-------|
| **Llama** | Fast | High | Pay-per-use | Easy |
| Gemini | Fast | High | Pay-per-use | Easy |
| Ollama | Medium | Good | Free | Complex |

## Troubleshooting

### API Key Not Found

```
Llama API key not provided. Set LLAMA_API_KEY environment variable or update config.
```

**Solution**: set the environment variable or use the setup script.

### Network/API Errors

```
Llama analysis error: HTTPSConnectionPool...
```

**Solution**: check your internet connection and API key validity.

### JSON Parsing Errors

```
Failed to parse Llama JSON response
```

**Solution**: usually temporary - the tool will retry automatically.

### Rate Limit Errors

**Solution**: reduce `parallel_workers` or increase `rate_limit_delay`.

### Timeout Errors

**Solution**: increase `fast_timeout` or reduce `batch_size`.

### Memory Issues

**Solution**: reduce `parallel_workers` and enable `fast_mode`.

### Slow Performance

**Solution**: increase `parallel_workers` and reduce delays.

### Debug Mode

```bash
# Enable detailed performance logging
python ai_instagram_organizer.py --source "/path" --debug-performance
```

Add debug logging to see detailed API responses:

```bash
python -c "import logging; logging.basicConfig(level=logging.DEBUG)"
```

## Future Optimizations

- **Async processing** for even higher concurrency
- **GPU acceleration** for image preprocessing
- **Distributed processing** across multiple machines
- **Predictive batching** based on image complexity
- **Auto-tuning** based on system capabilities

## Security Notes

- Never commit API keys to version control
- Use environment variables for production deployments
- Rotate API keys regularly
- Monitor your API usage and costs

## Support

If you encounter issues:

1. Run the test script: `python tests/test_llama_api.py`
2. Check the [Llama API documentation](https://api.llama.com/docs)
3. Verify your API key is valid and has sufficient credits
4. Try switching to Gemini as a fallback option
