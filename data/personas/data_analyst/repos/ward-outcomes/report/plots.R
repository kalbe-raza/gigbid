library(ggplot2)
ggplot(readm, aes(x = ward, y = rate)) + geom_col() + coord_flip()
