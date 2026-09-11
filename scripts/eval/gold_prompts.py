"""Gold prompt test set: 50 diverse prompts from real chat-ai conversations.

Each prompt has: category, influencer_id, user_message, and expected qualities.
Used by the eval harness to score v2 response quality via Langfuse.
"""

GOLD_PROMPTS = [
    # Companion / Friendship (most popular category)
    {
        "category": "companion",
        "influencer_id": "qi6gd-esmrx-v2oyd-7fwhm-ibfs5-trflm-xm3iy-xq6d3-3hmwu-jb7tk-5qe",
        "message": "Hey Tara, how's your day going?",
        "expect": "casual, warm, in-character",
    },
    {
        "category": "companion",
        "influencer_id": "qi6gd-esmrx-v2oyd-7fwhm-ibfs5-trflm-xm3iy-xq6d3-3hmwu-jb7tk-5qe",
        "message": "I'm feeling lonely today, can we just talk?",
        "expect": "empathetic, supportive",
    },
    {
        "category": "companion",
        "influencer_id": "qi6gd-esmrx-v2oyd-7fwhm-ibfs5-trflm-xm3iy-xq6d3-3hmwu-jb7tk-5qe",
        "message": "What do you think about long-distance relationships?",
        "expect": "thoughtful opinion, stays in character",
    },
    {
        "category": "companion",
        "influencer_id": "qi6gd-esmrx-v2oyd-7fwhm-ibfs5-trflm-xm3iy-xq6d3-3hmwu-jb7tk-5qe",
        "message": "Mujhe aaj bahut bore ho raha hai, kuch mazedaar batao",
        "expect": "hinglish response, fun tone",
    },
    {
        "category": "companion",
        "influencer_id": "qi6gd-esmrx-v2oyd-7fwhm-ibfs5-trflm-xm3iy-xq6d3-3hmwu-jb7tk-5qe",
        "message": "Tell me something nobody knows about you",
        "expect": "creative, personal, in-character",
    },
    # Health / Fitness
    {
        "category": "health",
        "influencer_id": None,
        "message": "I want to lose 10kg in 3 months, what should I do?",
        "expect": "practical advice, no medical claims",
    },
    {
        "category": "fitness",
        "influencer_id": None,
        "message": "Kya gym me related help kar sakte ho?",
        "expect": "helpful, language-mirrored",
    },
    {
        "category": "health",
        "influencer_id": None,
        "message": "What's a good morning routine for energy?",
        "expect": "actionable tips, concise",
    },
    # Astrology / Spiritual
    {
        "category": "astrology",
        "influencer_id": "azjhl-m7isb-qfocx-md5sm-z55f2-zm5qf-lss57-5zdns-ljyy4-wfv2x-rae",
        "message": "I want to know about my wife and career",
        "expect": "astrological framing, asks for birth details",
    },
    {
        "category": "astrology",
        "influencer_id": None,
        "message": "Aaj mera din kaisa rahega?",
        "expect": "positive prediction, engaging",
    },
    {
        "category": "spirituality",
        "influencer_id": None,
        "message": "How do I find inner peace during stressful times?",
        "expect": "calming, practical spiritual advice",
    },
    # Education / Knowledge
    {
        "category": "education",
        "influencer_id": None,
        "message": "Future technology ke baare mein kya predictions hain?",
        "expect": "informative, engaging, hinglish-aware",
    },
    {
        "category": "education",
        "influencer_id": None,
        "message": "Explain quantum computing in simple terms",
        "expect": "simple analogy, not jargon-heavy",
    },
    {
        "category": "education",
        "influencer_id": None,
        "message": "Tell me about the Ghoomar dance. What makes it so special?",
        "expect": "culturally aware, detailed",
    },
    {
        "category": "education",
        "influencer_id": None,
        "message": "Can you help me prepare for UPSC prelims?",
        "expect": "structured advice, encouragement",
    },
    # Business / Career
    {
        "category": "business",
        "influencer_id": None,
        "message": "I need strategic guidance on scaling my startup",
        "expect": "actionable business advice",
    },
    {
        "category": "business",
        "influencer_id": None,
        "message": "Kya chal raha hai market mein, koi naya startup idea hai?",
        "expect": "trendy ideas, hinglish",
    },
    {
        "category": "business",
        "influencer_id": None,
        "message": "How do I build a personal brand on social media?",
        "expect": "step-by-step tips",
    },
    # Entertainment / Fashion
    {
        "category": "entertainment",
        "influencer_id": None,
        "message": "Recommend me a good Bollywood movie to watch tonight",
        "expect": "specific recommendation, brief reason",
    },
    {
        "category": "fashion",
        "influencer_id": None,
        "message": "What should I wear for a first date?",
        "expect": "style tips, asks about occasion",
    },
    {
        "category": "fashion",
        "influencer_id": None,
        "message": "Latest fashion trends kya hain 2026 mein?",
        "expect": "current trends, hinglish",
    },
    # Family / Relationships
    {
        "category": "family",
        "influencer_id": None,
        "message": "My parents don't understand my career choice, what do I do?",
        "expect": "empathetic, balanced advice",
    },
    {
        "category": "relationship",
        "influencer_id": None,
        "message": "How do I know if someone really likes me?",
        "expect": "relatable signs, conversational",
    },
    {
        "category": "relationship",
        "influencer_id": None,
        "message": "Breakup ke baad kaise move on karu?",
        "expect": "supportive, practical steps, hinglish",
    },
    # Romance
    {
        "category": "romance",
        "influencer_id": None,
        "message": "Write me a sweet good morning message for my girlfriend",
        "expect": "romantic, creative text",
    },
    {
        "category": "romance",
        "influencer_id": None,
        "message": "What's the most romantic thing someone can do?",
        "expect": "thoughtful, personal-feeling answer",
    },
    # Social / Lifestyle
    {
        "category": "social",
        "influencer_id": None,
        "message": "How do I make new friends as an adult?",
        "expect": "practical social tips",
    },
    {
        "category": "lifestyle",
        "influencer_id": None,
        "message": "What's your morning routine like?",
        "expect": "in-character personal answer",
    },
    {
        "category": "lifestyle",
        "influencer_id": None,
        "message": "Suggest a weekend plan for someone in Mumbai",
        "expect": "location-specific, fun options",
    },
    # Cultural / Arts
    {
        "category": "arts_and_culture",
        "influencer_id": None,
        "message": "Tell me about Mughal architecture",
        "expect": "informative, engaging",
    },
    {
        "category": "arts_culture",
        "influencer_id": None,
        "message": "Poetry sunao mujhe",
        "expect": "actual poetry or creative response, hindi",
    },
    # Food
    {
        "category": "food_and_drink",
        "influencer_id": None,
        "message": "Best street food in Delhi?",
        "expect": "specific recommendations, enthusiastic",
    },
    {
        "category": "food_and_drink",
        "influencer_id": None,
        "message": "Kuch khane ka man kar raha hai, kya banau?",
        "expect": "recipe suggestion, casual hinglish",
    },
    # Technology
    {
        "category": "technology",
        "influencer_id": None,
        "message": "Which phone should I buy under 20000 rupees?",
        "expect": "specific models, brief comparison",
    },
    {
        "category": "technology",
        "influencer_id": None,
        "message": "AI se meri job chali jayegi kya?",
        "expect": "balanced view, reassuring, hinglish",
    },
    # Travel
    {
        "category": "travel",
        "influencer_id": None,
        "message": "Plan a 3-day trip to Goa for me",
        "expect": "day-by-day itinerary, practical",
    },
    {
        "category": "travel",
        "influencer_id": None,
        "message": "Cheapest way to travel from Delhi to Manali?",
        "expect": "transport options with costs",
    },
    # Beauty / Skincare
    {
        "category": "beauty",
        "influencer_id": None,
        "message": "Mujhe nahi pata meri kaisi skin hai",
        "expect": "asks clarifying questions, helpful",
    },
    {
        "category": "beauty",
        "influencer_id": None,
        "message": "How to get rid of dark circles?",
        "expect": "practical skincare tips",
    },
    # Gaming / Fantasy
    {
        "category": "gaming",
        "influencer_id": None,
        "message": "What's the best strategy for winning in BGMI?",
        "expect": "game-specific tactical advice",
    },
    {
        "category": "fantasy",
        "influencer_id": None,
        "message": "Tell me a story about a magical kingdom",
        "expect": "creative storytelling",
    },
    # Hinglish stress tests
    {
        "category": "companion",
        "influencer_id": None,
        "message": "Yaar aaj mood off hai, kuch funny suna do",
        "expect": "joke or funny content, hinglish",
    },
    {
        "category": "companion",
        "influencer_id": None,
        "message": "Tum real ho ya AI ho?",
        "expect": "stays in character, doesn't break",
    },
    {
        "category": "companion",
        "influencer_id": None,
        "message": "Mera naam Rahul hai, yaad rakhna",
        "expect": "acknowledges name, promises to remember",
    },
    # Edge cases
    {
        "category": "companion",
        "influencer_id": None,
        "message": "...",
        "expect": "handles minimal input gracefully",
    },
    {
        "category": "companion",
        "influencer_id": None,
        "message": "What is 2 + 2?",
        "expect": "answers correctly, stays in character",
    },
    {
        "category": "companion",
        "influencer_id": None,
        "message": "Translate 'I love you' to Hindi",
        "expect": "correct translation",
    },
    # Multilingual
    {
        "category": "companion",
        "influencer_id": None,
        "message": "నువ్వు ఏమి చేస్తున్నావ్?",
        "expect": "Telugu response or acknowledgment",
    },
    {
        "category": "companion",
        "influencer_id": None,
        "message": "Namaste, kaise ho aap?",
        "expect": "warm Hindi greeting response",
    },
]
