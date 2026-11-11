from pinecone import Pinecone

pc = Pinecone(api_key=settings.PINECONE_KEY)
index = pc.Index("kb-articles")


def upsert(chunks):
    index.upsert(vectors=[(c.id, c.vector, {"article": c.article}) for c in chunks])
